from datetime import datetime
import json
from zoneinfo import ZoneInfo

from app.core import settings
from app.dhan import DhanService


def test_dhan_renewal_persists_token_and_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "dhan.json"
    monkeypatch.setattr(settings, "dhan_client_id", "test-client")
    monkeypatch.setattr(settings, "dhan_access_token", "old-token")
    monkeypatch.setattr(settings, "dhan_access_token_file", str(path))
    monkeypatch.setattr(settings, "dhan_auto_renew_enabled", True)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"accessToken": "new-token"}

    def fake_get(url, headers, timeout):
        calls.append((url, headers))
        return Response()

    monkeypatch.setattr("app.dhan.httpx.get", fake_get)
    service = DhanService()
    assert service.renew_token_if_due()["status"] == "renewed"
    assert service._headers()["access-token"] == "new-token"
    assert json.loads(path.read_text())["accessToken"] == "new-token"
    assert DhanService()._headers()["access-token"] == "new-token"
    assert service.renew_token_if_due()["status"] == "current"
    assert len(calls) == 1


def test_dhan_renewal_fails_closed_when_persistence_missing(monkeypatch):
    monkeypatch.setattr(settings, "dhan_client_id", "test-client")
    monkeypatch.setattr(settings, "dhan_access_token", "old-token")
    monkeypatch.setattr(settings, "dhan_access_token_file", "")
    monkeypatch.setattr(settings, "dhan_auto_renew_enabled", True)
    service = DhanService()
    assert service.renew_token_if_due()["status"] == "not_configured"
    assert service._headers()["access-token"] == "old-token"
