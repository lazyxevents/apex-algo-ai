from kiteconnect import KiteConnect

from .core import settings


class KiteService:
    def __init__(self):
        self.api_key = settings.kite_api_key
        self.api_secret = settings.kite_api_secret
        self.access_token = settings.kite_access_token
        self.kite = KiteConnect(api_key=self.api_key) if self.api_key else None
        if self.kite and self.access_token:
            self.kite.set_access_token(self.access_token)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def login_url(self) -> str | None:
        if not self.kite:
            return None
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> dict:
        if not self.kite or not self.api_secret:
            return {"ok": False, "error": "KITE_API_KEY/KITE_API_SECRET not configured"}
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            token = data.get("access_token", "")
            if token:
                self.kite.set_access_token(token)
                self.access_token = token
            return {"ok": True, "access_token": token, "user_name": data.get("user_name")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def profile(self) -> dict:
        if not self.kite or not self.access_token:
            return {"connected": False, "reason": "No active access token"}
        try:
            return {"connected": True, "profile": self.kite.profile()}
        except Exception as exc:
            return {"connected": False, "reason": str(exc)}

    def connection_status(self) -> dict:
        profile = self.profile()
        return {"provider": "zerodha", **profile}


kite_service = KiteService()
