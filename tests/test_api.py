from fastapi.testclient import TestClient

from rag_sync.api import create_app


def test_health_endpoint():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_profiles_endpoint():
    client = TestClient(create_app())

    response = client.get("/api/profiles")

    assert response.status_code == 200
    assert isinstance(response.json()["profiles"], list)
