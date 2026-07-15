import json

from app.api.notifications import NotificationSubscriptionStore


class FakeSesClient:
    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "ses-123"}


class FakeDiscordResponse:
    status_code = 204

    def raise_for_status(self):
        return None


class FakeDiscordClient:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeDiscordResponse()


def test_store_masks_email_and_sends_only_selected_ship_digest(tmp_path):
    ses = FakeSesClient()
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        ses_from_email="sender@example.com",
        ses_client_factory=lambda: ses,
    )
    created = store.create("email", "captain@example.com", ["HW-002"])

    assert created["destination_masked"] == "c***@example.com"
    assert "destination" not in created
    assert "captain@example.com" not in json.dumps(store.list_public())

    result = store.send_digest(created["id"], [
        {"ship_id": "HW-001", "ship_name": "Alpha", "status": "action", "speed_loss_pct": 8.2,
         "excess_cost_per_day": 4100},
        {"ship_id": "HW-002", "ship_name": "Bravo", "status": "watch", "speed_loss_pct": 5.1,
         "excess_cost_per_day": 2200},
    ])

    assert result["delivered"] is True
    assert result["ship_count"] == 1
    assert ses.calls[0]["Destination"]["ToAddresses"] == ["captain@example.com"]
    assert "Bravo" in ses.calls[0]["Message"]["Body"]["Text"]["Data"]
    assert "Alpha" not in ses.calls[0]["Message"]["Body"]["Text"]["Data"]


def test_store_uses_system_discord_webhook_without_exposing_it(tmp_path):
    discord = FakeDiscordClient()
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        discord_webhook_url="https://discord.example/secret",
        discord_client_factory=lambda: discord,
    )
    created = store.create("discord", None, ["HW-001"])

    result = store.send_digest(created["id"], [
        {"ship_id": "HW-001", "ship_name": "Alpha", "status": "action", "speed_loss_pct": 8.2,
         "excess_cost_per_day": 4100},
    ])

    assert created["destination_masked"] == "系統 Discord 頻道"
    assert result["delivered"] is True
    assert discord.calls[0][0] == "https://discord.example/secret"
    assert "secret" not in json.dumps(store.list_public())


def test_store_ignores_malformed_subscription_records(tmp_path):
    path = tmp_path / "subscriptions.json"
    path.write_text(json.dumps([{"id": "broken"}, "not-an-object"]))

    store = NotificationSubscriptionStore(path)

    assert store.list_public() == []


def test_discord_subscription_with_personal_webhook(tmp_path):
    discord = FakeDiscordClient()
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        discord_webhook_url="",  # 系統頻道未設定，訂閱自填照樣可用
        discord_client_factory=lambda: discord,
    )
    url = "https://discord.com/api/webhooks/123456789/abcDEF_ghi-JKL"
    created = store.create("discord", url, ["HW-001"])

    # 遮罩：不外洩完整 webhook，只留尾碼辨識
    assert url not in json.dumps(store.list_public())
    assert created["destination_masked"].endswith("-JKL）")

    result = store.send_digest(created["id"], [
        {"ship_id": "HW-001", "ship_name": "Alpha", "status": "action",
         "speed_loss_pct": 12.0, "excess_cost_per_day": 9000},
    ])
    assert result["delivered"] is True
    assert discord.calls[0][0] == url  # 寄到訂閱者自己的 webhook


def test_discord_rejects_invalid_webhook_and_status_is_self_service(tmp_path):
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        ses_from_email="", discord_webhook_url="",
    )
    try:
        store.create("discord", "https://evil.example/steal", ["HW-001"])
        raise AssertionError("非 Discord webhook 網址應被拒絕")
    except ValueError as exc:
        assert "Discord Webhook" in str(exc)
    assert store.channel_status() == {"ses": "not_configured", "discord": "self_service"}


def _ships_status():
    return [
        {"ship_id": "HW-001", "ship_name": "Alpha", "status": "watch",
         "speed_loss_pct": 6.2, "excess_cost_per_day": 4100},
        {"ship_id": "HW-002", "ship_name": "Bravo", "status": "ok",
         "speed_loss_pct": 2.1, "excess_cost_per_day": 800},
    ]


def test_welcome_notification_on_create(tmp_path):
    ses = FakeSesClient()
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        ses_from_email="sender@example.com", ses_client_factory=lambda: ses,
    )
    created = store.create("email", "captain@example.com", ["HW-001"], kind="alert")
    assert created["kind"] == "alert"
    result = store.send_welcome(created["id"], _ships_status(), 5.0)
    assert result["delivered"] is True
    body = ses.calls[0]["Message"]["Body"]["Text"]["Data"]
    assert "訂閱確認" in body and "預警通知" in body and "Alpha" in body


def test_noon_report_updates_digest_vs_alert_threshold(tmp_path):
    ses = FakeSesClient()
    store = NotificationSubscriptionStore(
        tmp_path / "subscriptions.json",
        ses_from_email="sender@example.com", ses_client_factory=lambda: ses,
    )
    digest_sub = store.create("email", "d@example.com", ["HW-001"])          # 預設 digest
    alert_sub = store.create("email", "a@example.com", ["HW-001"], kind="alert")

    # 更新後 SL 3.0：digest 照寄、alert 低於門檻靜默
    low = [{"ship_id": "HW-001", "ship_name": "Alpha",
            "report_date": "2026-07-15", "speed_loss_pct": 3.0}]
    out = store.notify_noon_report_updates(low, _ships_status(), 5.0)
    assert out["notified"] == 1
    statuses = {r["id"]: r.get("status") for r in out["results"]}
    assert statuses[alert_sub["id"]] == "below_threshold"
    assert ses.calls[-1]["Destination"]["ToAddresses"] == ["d@example.com"]

    # 更新後 SL 8.0：兩者都寄；alert 主旨帶預警
    high = [{"ship_id": "HW-001", "ship_name": "Alpha",
             "report_date": "2026-07-15", "speed_loss_pct": 8.0}]
    out = store.notify_noon_report_updates(high, _ships_status(), 5.0)
    assert out["notified"] == 2
    subjects = [c["Message"]["Subject"]["Data"] for c in ses.calls]
    assert any("預警" in s for s in subjects)

    # 沒訂閱的船更新：兩者都靜默
    other = [{"ship_id": "HW-002", "ship_name": "Bravo",
              "report_date": "2026-07-15", "speed_loss_pct": 9.9}]
    out = store.notify_noon_report_updates(other, _ships_status(), 5.0)
    assert out["notified"] == 0 and out["results"] == []


def test_invalid_kind_rejected(tmp_path):
    store = NotificationSubscriptionStore(tmp_path / "subscriptions.json")
    try:
        store.create("email", "x@example.com", ["HW-001"], kind="hourly")
        raise AssertionError("未知訂閱類型應被拒絕")
    except ValueError as exc:
        assert "訂閱類型" in str(exc)
