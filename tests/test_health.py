from fastapi.testclient import TestClient

from vigia.app import create_app
from vigia.config import Settings


def test_health_endpoint_answers_ok() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/salud")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
