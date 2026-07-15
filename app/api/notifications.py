"""Persistent per-ship newsletter subscriptions and explicit SES/Discord delivery."""

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
        ses_client_factory: Callable[[], object] | None = None,
        discord_client_factory: Callable[[], object] | None = None,
    ):
        self.path = Path(path)
        self._lock = RLock()
        self.ses_from_email = config.SES_FROM_EMAIL if ses_from_email is None else ses_from_email
        self.discord_webhook_url = config.DISCORD_WEBHOOK_URL if discord_webhook_url is None else discord_webhook_url
        self.ses_client_factory = ses_client_factory or (
            lambda: boto3.client("ses", region_name=config.SES_REGION)
        )
        self.discord_client_factory = discord_client_factory or (
            lambda: httpx.Client(timeout=8, follow_redirects=True)
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
            "destination_masked": masked,
            "ship_ids": list(subscription["ship_ids"]),
            "created_at": subscription["created_at"],
        }

    def list_public(self) -> list[dict]:
        return [self._public(item) for item in self._load()]

    def create(self, channel: str, destination: str | None, ship_ids: list[str]) -> dict:
        if channel not in {"email", "discord"}:
            raise ValueError("通知通道只接受 email 或 discord")
        normalized_destination = (destination or "").strip()
        if channel == "email" and not EMAIL_PATTERN.fullmatch(normalized_destination):
            raise ValueError("請輸入有效的 Email 收件地址")
        if channel == "discord" and normalized_destination \
                and not DISCORD_WEBHOOK_PATTERN.fullmatch(normalized_destination):
            raise ValueError("請輸入有效的 Discord Webhook URL（https://discord.com/api/webhooks/…）")
        subscription = {
            "id": uuid4().hex,
            "channel": channel,
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
            "ses": "configured" if self.ses_from_email else "not_configured",
            # discord 一律可用：系統 webhook（configured）或訂閱者自填（self_service）
            "discord": "configured" if self.discord_webhook_url else "self_service",
        }

    def send_digest(self, subscription_id: str, ships: list[dict]) -> dict:
        subscription = next((item for item in self._load() if item["id"] == subscription_id), None)
        if subscription is None:
            raise KeyError(subscription_id)
        selected = [ship for ship in ships if ship["ship_id"] in subscription["ship_ids"]]
        lines = ["HullWatch 船隊摘要", ""]
        if selected:
            lines.extend(
                f"{ship['ship_name']} ({ship['ship_id']})｜{ship['status']}｜"
                f"Speed Loss {ship['speed_loss_pct']:.1f}%｜"
                f"超額成本 US${ship['excess_cost_per_day']:,.0f}/日"
                for ship in selected
            )
        else:
            lines.append("目前訂閱範圍內沒有船舶資料。")
        message = "\n".join(lines)
        channel = subscription["channel"]

        if channel == "email":
            if not self.ses_from_email:
                return {"delivered": False, "status": "not_configured", "ship_count": len(selected)}
            response = self.ses_client_factory().send_email(
                Source=self.ses_from_email,
                Destination={"ToAddresses": [subscription["destination"]]},
                Message={
                    "Subject": {"Data": "HullWatch 船隊效能摘要", "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": message, "Charset": "UTF-8"}},
                },
            )
            message_id = response.get("MessageId")
        else:
            # 訂閱自填 webhook 優先；留空時退回系統頻道（向後相容）
            webhook_url = subscription.get("destination") or self.discord_webhook_url
            if not webhook_url:
                return {"delivered": False, "status": "not_configured", "ship_count": len(selected)}
            client = self.discord_client_factory()
            try:
                response = client.post(webhook_url, json={"content": message[:2000]})
                response.raise_for_status()
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()
            message_id = None

        return {
            "delivered": True,
            "status": "delivered",
            "channel": channel,
            "ship_count": len(selected),
            "message_id": message_id,
        }
