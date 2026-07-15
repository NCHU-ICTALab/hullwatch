"""Persistent per-ship notification subscriptions (digest/alert) with SQS-relay/SES/Discord delivery."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

import boto3
import httpx

from app import config

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DISCORD_WEBHOOK_PATTERN = re.compile(
    r"^https://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/[\w-]+$"
)


def _mask_email(value: str) -> str:
    local, domain = value.split("@", 1)
    return f"{local[:1]}***@{domain}"


def _mask_webhook(value: str) -> str:
    return f"Discord webhook（…{value[-4:]}）"


class NotificationSubscriptionStore:
    """Small JSON-backed store suitable for the no-auth competition demo."""

    def __init__(
        self,
        path: Path,
        *,
        ses_from_email: str | None = None,
        discord_webhook_url: str | None = None,
        email_queue_url: str | None = None,
        email_queue_from: str | None = None,
        ses_client_factory: Callable[[], object] | None = None,
        discord_client_factory: Callable[[], object] | None = None,
        sqs_client_factory: Callable[[], object] | None = None,
    ):
        self.path = Path(path)
        self._lock = RLock()
        self.ses_from_email = config.SES_FROM_EMAIL if ses_from_email is None else ses_from_email
        self.discord_webhook_url = config.DISCORD_WEBHOOK_URL if discord_webhook_url is None else discord_webhook_url
        # SQS 寄信中繼（團隊第二帳號的 emailQueue，自建）：設定後 email 一律走中繼，
        # 收件者不受 SES sandbox 驗證限制；留空退回直寄 SES。
        self.email_queue_url = config.EMAIL_QUEUE_URL if email_queue_url is None else email_queue_url
        self.email_queue_from = config.EMAIL_QUEUE_FROM if email_queue_from is None else email_queue_from
        self.ses_client_factory = ses_client_factory or (
            lambda: boto3.client("ses", region_name=config.SES_REGION)
        )
        self.discord_client_factory = discord_client_factory or (
            lambda: httpx.Client(timeout=8, follow_redirects=True)
        )
        self.sqs_client_factory = sqs_client_factory or (
            lambda: boto3.client("sqs", region_name="us-east-1")
        )

    def _load(self) -> list[dict]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                return []
            return [
                item for item in value
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item.get("channel") in {"email", "discord"}
                and isinstance(item.get("ship_ids"), list)
                and all(isinstance(ship_id, str) for ship_id in item["ship_ids"])
                and isinstance(item.get("created_at"), str)
                and (item["channel"] == "discord" or isinstance(item.get("destination"), str))
            ]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save(self, subscriptions: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(subscriptions, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _public(subscription: dict) -> dict:
        channel = subscription["channel"]
        destination = subscription.get("destination") or ""
        if channel == "email":
            masked = _mask_email(destination)
        else:
            masked = _mask_webhook(destination) if destination else "系統 Discord 頻道"
        return {
            "id": subscription["id"],
            "channel": channel,
            "kind": subscription.get("kind", "digest"),
            "destination_masked": masked,
            "ship_ids": list(subscription["ship_ids"]),
            "created_at": subscription["created_at"],
        }

    def list_public(self) -> list[dict]:
        return [self._public(item) for item in self._load()]

    def create(self, channel: str, destination: str | None, ship_ids: list[str],
               kind: str = "digest") -> dict:
        if channel not in {"email", "discord"}:
            raise ValueError("通知通道只接受 email 或 discord")
        if kind not in {"digest", "alert"}:
            raise ValueError("訂閱類型只接受 digest（每日摘要）或 alert（預警）")
        normalized_destination = (destination or "").strip()
        if channel == "email" and not EMAIL_PATTERN.fullmatch(normalized_destination):
            raise ValueError("請輸入有效的 Email 收件地址")
        if channel == "discord" and normalized_destination \
                and not DISCORD_WEBHOOK_PATTERN.fullmatch(normalized_destination):
            raise ValueError("請輸入有效的 Discord Webhook URL（https://discord.com/api/webhooks/…）")
        subscription = {
            "id": uuid4().hex,
            "channel": channel,
            "kind": kind,  # digest＝每日摘要；alert＝預警（SL 越過留意門檻才寄）
            # discord：自填 webhook（空值＝沿用系統頻道，向後相容）
            "destination": normalized_destination or None,
            "ship_ids": list(dict.fromkeys(ship_ids)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            subscriptions = self._load()
            subscriptions.append(subscription)
            self._save(subscriptions)
        return self._public(subscription)

    def delete(self, subscription_id: str) -> None:
        with self._lock:
            subscriptions = self._load()
            remaining = [item for item in subscriptions if item["id"] != subscription_id]
            if len(remaining) == len(subscriptions):
                raise KeyError(subscription_id)
            self._save(remaining)

    def channel_status(self) -> dict[str, str]:
        return {
            "ses": "configured" if (self.email_queue_url or self.ses_from_email) else "not_configured",
            # discord 一律可用：系統 webhook（configured）或訂閱者自填（self_service）
            "discord": "configured" if self.discord_webhook_url else "self_service",
        }

    # ---------- 寄送機制（SES／Discord 共用） ----------

    def _deliver(self, subscription: dict, subject: str, message: str) -> dict:
        channel = subscription["channel"]
        if channel == "email":
            if self.email_queue_url:  # 自建 SQS 中繼（SESv2 payload），免 sandbox 驗證
                payload = {
                    "FromEmailAddress": self.email_queue_from,
                    "Destination": {"ToAddresses": [subscription["destination"]]},
                    **({"ReplyToAddresses": [self.ses_from_email]} if self.ses_from_email else {}),
                    "Content": {"Simple": {
                        "Subject": {"Charset": "UTF-8", "Data": subject},
                        "Body": {"Text": {"Charset": "UTF-8", "Data": message}},
                    }},
                }
                response = self.sqs_client_factory().send_message(
                    QueueUrl=self.email_queue_url,
                    MessageBody=json.dumps(payload, ensure_ascii=False),
                )
                return {"delivered": True, "status": "delivered", "channel": channel,
                        "via": "sqs-relay", "message_id": response.get("MessageId")}
            if not self.ses_from_email:
                return {"delivered": False, "status": "not_configured", "channel": channel}
            response = self.ses_client_factory().send_email(
                Source=self.ses_from_email,
                Destination={"ToAddresses": [subscription["destination"]]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": message, "Charset": "UTF-8"}},
                },
            )
            return {"delivered": True, "status": "delivered", "channel": channel,
                    "message_id": response.get("MessageId")}
        # 訂閱自填 webhook 優先；留空時退回系統頻道（向後相容）
        webhook_url = subscription.get("destination") or self.discord_webhook_url
        if not webhook_url:
            return {"delivered": False, "status": "not_configured", "channel": channel}
        client = self.discord_client_factory()
        try:
            response = client.post(webhook_url, json={"content": message[:2000]})
            response.raise_for_status()
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        return {"delivered": True, "status": "delivered", "channel": channel, "message_id": None}

    @staticmethod
    def _ship_line(ship: dict) -> str:
        return (f"{ship['ship_name']} ({ship['ship_id']})｜{ship['status']}｜"
                f"Speed Loss {ship['speed_loss_pct']:.1f}%｜"
                f"超額成本 US${ship['excess_cost_per_day']:,.0f}/日")

    def _digest_message(self, subscription: dict, ships: list[dict], header: str) -> tuple[str, int]:
        selected = [ship for ship in ships if ship["ship_id"] in subscription["ship_ids"]]
        lines = [header, ""]
        if selected:
            lines.extend(self._ship_line(ship) for ship in selected)
        else:
            lines.append("目前訂閱範圍內沒有船舶資料。")
        return "\n".join(lines), len(selected)

    # ---------- SES sandbox 收件者驗證 ----------

    def email_verification_status(self, email: str) -> str:
        """success｜pending｜none｜unknown（無查詢權限或呼叫失敗）。"""
        try:
            attrs = self.ses_client_factory().get_identity_verification_attributes(
                Identities=[email])
            status = attrs.get("VerificationAttributes", {}).get(email, {}).get(
                "VerificationStatus")
            return (status or "none").lower()
        except Exception:  # noqa: BLE001
            return "unknown"

    def request_email_verification(self, email: str) -> bool:
        """觸發 AWS 驗證信（sandbox 下新收件者的必經流程）。"""
        try:
            self.ses_client_factory().verify_email_identity(EmailAddress=email)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------- 各類通知 ----------

    def send_digest(self, subscription_id: str, ships: list[dict]) -> dict:
        subscription = next((item for item in self._load() if item["id"] == subscription_id), None)
        if subscription is None:
            raise KeyError(subscription_id)
        message, count = self._digest_message(subscription, ships, "HullWatch 船隊摘要")
        result = self._deliver(subscription, "HullWatch 船隊效能摘要", message)
        return {**result, "ship_count": count}

    def send_welcome(self, subscription_id: str, ships: list[dict],
                     watch_threshold: float) -> dict:
        """訂閱建立當下的確認通知（規則 2）：附訂閱船舶目前狀態。"""
        subscription = next((item for item in self._load() if item["id"] == subscription_id), None)
        if subscription is None:
            raise KeyError(subscription_id)
        kind_label = ("每日摘要通知" if subscription.get("kind", "digest") == "digest"
                      else f"預警通知（Speed Loss > {watch_threshold:g}% 才通知）")
        message, count = self._digest_message(
            subscription, ships,
            f"HullWatch 訂閱確認：{kind_label}\n訂閱船舶目前狀態如下——")
        result = self._deliver(subscription, "HullWatch 訂閱確認", message)
        return {**result, "ship_count": count}

    def send_welcome_or_request_verification(self, subscription_id: str, ships: list[dict],
                                             watch_threshold: float) -> dict:
        """訂閱確認：email 收件者未通過 SES sandbox 驗證時，改寄 AWS 驗證信並如實回報。

        新信箱第一次訂閱本來會被 SES 硬拒（Email address is not verified）——
        這裡把它變成引導流程：verification_sent → 使用者點驗證連結 → 之後通知照常。
        """
        subscription = next((item for item in self._load() if item["id"] == subscription_id), None)
        if subscription is None:
            raise KeyError(subscription_id)
        if subscription["channel"] == "email" and self.ses_from_email \
                and not self.email_queue_url:  # 中繼免驗證，直接寄
            status = self.email_verification_status(subscription["destination"])
            if status == "unknown":
                # 無查詢權限：直接嘗試寄，被 sandbox 拒收再退驗證流程
                try:
                    return self.send_welcome(subscription_id, ships, watch_threshold)
                except Exception as exc:  # noqa: BLE001
                    if "not verified" not in str(exc):
                        raise
                    status = "none"
            if status != "success":
                if self.request_email_verification(subscription["destination"]):
                    return {"delivered": False, "status": "verification_sent",
                            "channel": "email"}
                return {"delivered": False, "status": "verification_unavailable",
                        "channel": "email"}
        return self.send_welcome(subscription_id, ships, watch_threshold)

    def notify_noon_report_updates(self, updates: list[dict], ships: list[dict],
                                   watch_threshold: float) -> dict:
        """上傳新正午日報後的自動通知（規則 1/3）。

        updates：本次被接受的日報 [{ship_id, ship_name, report_date, speed_loss_pct}, …]。
        digest 訂閱：訂閱船舶有更新就寄整份摘要；alert 訂閱：只有更新後
        Speed Loss > watch_threshold 的船才寄，否則靜默。回傳彙整，永不 raise。
        """
        results = []
        notified = 0
        updated_ids = {u["ship_id"] for u in updates}
        for subscription in self._load():
            covered = [u for u in updates if u["ship_id"] in subscription["ship_ids"]]
            if not covered:
                continue
            kind = subscription.get("kind", "digest")
            try:
                if kind == "digest":
                    names = "、".join(sorted({u["ship_id"] for u in covered}))
                    message, _ = self._digest_message(
                        subscription, ships,
                        f"HullWatch 摘要（新正午日報：{names}）")
                    result = self._deliver(subscription, "HullWatch 船隊摘要（日報更新）", message)
                else:
                    over = [u for u in covered
                            if float(u.get("speed_loss_pct") or 0) > watch_threshold]
                    if not over:
                        results.append({"id": subscription["id"], "kind": kind,
                                        "status": "below_threshold"})
                        continue
                    lines = [f"HullWatch 預警：Speed Loss 超過留意門檻 {watch_threshold:g}%", ""]
                    lines.extend(
                        f"{u.get('ship_name', u['ship_id'])} ({u['ship_id']})｜"
                        f"日報 {u.get('report_date', '')}｜Speed Loss {float(u['speed_loss_pct']):.1f}%"
                        for u in over)
                    lines.append("\n建議檢視儀表板並評估清洗排程。")
                    result = self._deliver(
                        subscription,
                        f"【HullWatch 預警】{over[0]['ship_id']} 等 {len(over)} 艘 Speed Loss 越過留意門檻",
                        "\n".join(lines))
                notified += int(result.get("delivered", False))
                results.append({"id": subscription["id"], "kind": kind, **result})
            except Exception as exc:  # noqa: BLE001 —— 通知失敗不可拖垮日報上傳
                results.append({"id": subscription["id"], "kind": kind,
                                "status": "error", "reason": str(exc)[:120]})
        return {"updates": len(updates), "ships": sorted(updated_ids),
                "notified": notified, "results": results}
