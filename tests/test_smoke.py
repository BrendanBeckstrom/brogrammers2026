from fastapi.testclient import TestClient

from api import main
from api.main import app


def test_healthz() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_readyz() -> None:
    response = TestClient(app).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_version() -> None:
    response = TestClient(app).get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"version": main.APP_VERSION}
    assert main.APP_VERSION
