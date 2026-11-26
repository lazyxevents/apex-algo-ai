from app.main import app


def test_indian_forex_is_mounted_on_existing_backend():
    mounts = {route.path: route for route in app.routes if hasattr(route, "app")}
    assert "/api/indian-forex" in mounts


def test_indian_forex_health_and_mt5_routes_are_available():
    mount = next(route for route in app.routes if getattr(route, "path", None) == "/api/indian-forex")
    paths = {getattr(route, "path", "") for route in mount.app.routes}
    assert "/health" in paths
    assert "/api/v1/mt5/status" in paths
    assert "/api/v1/research/agents" in paths
