from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pywebpush import WebPushException, webpush
from sqlalchemy import func, select

from .core import SessionLocal, settings
from .models import PushSubscription


class PushNotificationService:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="apex-push")

    @property
    def configured(self) -> bool:
        return bool(
            settings.push_notifications_enabled
            and settings.vapid_public_key.strip()
            and settings.vapid_private_key.strip()
            and settings.vapid_subject.strip()
        )

    def snapshot(self) -> dict:
        with SessionLocal() as db:
            active = int(db.scalar(
                select(func.count()).select_from(PushSubscription).where(PushSubscription.active == 1)
            ) or 0)
        return {
            "enabled": settings.push_notifications_enabled,
            "configured": self.configured,
            "publicKey": settings.vapid_public_key if self.configured else "",
            "subscriberCount": active,
            "event": "trade.opened",
            "delivery": "web_push_service_worker",
        }

    def subscribe(self, subscription: dict, user_agent: str = "") -> dict:
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") or {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            raise ValueError("Invalid browser push subscription")

        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
            if row is None:
                row = PushSubscription(
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    user_agent=(user_agent or "")[:512],
                    active=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.p256dh = p256dh
                row.auth = auth
                row.user_agent = (user_agent or row.user_agent or "")[:512]
                row.active = 1
                row.updated_at = now
            db.commit()
        return self.snapshot()

    def unsubscribe(self, endpoint: str) -> dict:
        endpoint = str(endpoint or "").strip()
        if endpoint:
            with SessionLocal() as db:
                row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
                if row is not None:
                    row.active = 0
                    row.updated_at = datetime.now(timezone.utc)
                    db.commit()
        return self.snapshot()

    def notify_trade_opened_async(self, trade: dict) -> None:
        if not self.configured:
            return
        self._executor.submit(self._notify_trade_opened, trade)

    def _notify_trade_opened(self, trade: dict) -> None:
        trade_id = trade.get("id")
        direction = str(trade.get("direction") or "")
        name = str(trade.get("displayName") or trade.get("symbol") or "SENSEX option")
        quantity = int(trade.get("quantity") or 0)
        entry = float(trade.get("entry") or 0.0)
        stop = float(trade.get("stop") or 0.0)
        target = float(trade.get("target") or 0.0)
        strategy = str((trade.get("meta") or {}).get("chosenStrategy") or (trade.get("meta") or {}).get("strategy") or "")
        score = (trade.get("meta") or {}).get("signalScore")

        detail_bits = [
            f"{direction} • Qty {quantity} • Entry ₹{entry:.2f}",
            f"SL ₹{stop:.2f} • Target ₹{target:.2f}",
        ]
        if score is not None:
            try:
                detail_bits.append(f"Score {float(score):.2f}" + (f" • {strategy}" if strategy else ""))
            except (TypeError, ValueError):
                pass

        payload = {
            "type": "trade.opened",
            "title": f"APEX Trade Executed • {direction}",
            "body": f"{name}\n" + "\n".join(detail_bits),
            "tag": f"apex-trade-{trade_id}",
            "tradeId": trade_id,
            "url": "/#open-positions",
            "requireInteraction": True,
            "renotify": True,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        }

        with SessionLocal() as db:
            rows = list(db.execute(
                select(PushSubscription).where(PushSubscription.active == 1)
            ).scalars().all())

        private_key = settings.vapid_private_key.replace("\\n", "\n").strip()
        for row in rows:
            try:
                webpush(
                    subscription_info={
                        "endpoint": row.endpoint,
                        "keys": {"p256dh": row.p256dh, "auth": row.auth},
                    },
                    data=json.dumps(payload),
                    vapid_private_key=private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=120,
                )
            except WebPushException as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code in {404, 410}:
                    with SessionLocal() as db:
                        stale = db.scalar(select(PushSubscription).where(PushSubscription.id == row.id))
                        if stale is not None:
                            stale.active = 0
                            stale.updated_at = datetime.now(timezone.utc)
                            db.commit()
            except Exception:
                # Notification delivery must never interrupt or roll back a trade.
                continue


push_notification_service = PushNotificationService()
