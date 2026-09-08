from __future__ import annotations

from fastapi.testclient import TestClient


def test_live_always_ok(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_lists_all_dependencies(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code in (200, 503)
    names = {d["name"] for d in resp.json()["dependencies"]}
    assert names == {"database", "storage", "broker"}


def test_ready_ok_with_dev_defaults(client: TestClient) -> None:
    # SQLite + local storage + eager broker are all reachable with zero setup.
    resp = client.get("/health/ready")
    assert resp.status_code == 200, resp.json()
