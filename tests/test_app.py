"""POST /caso returns a valid Verdict, with the analyst mocked (T-07 hecho-cuando)."""

from fastapi.testclient import TestClient

from vigia.app import create_app
from vigia.config import Settings
from vigia.domain.analyst import AnalystResult
from vigia.domain.case import Case
from vigia.domain.verdict import Verdict


def test_post_case_returns_a_degraded_verdict_without_an_analyst() -> None:
    client = TestClient(create_app(Settings()))

    response = client.post(
        "/caso", json={"country": "CO", "text": "Gracias por su compra, su pedido va en camino."}
    )

    assert response.status_code == 200
    verdict = Verdict.model_validate(response.json())
    assert verdict.label == "doubtful"
    assert verdict.degraded is True


def test_post_case_uses_the_injected_analyst() -> None:
    def mock_analyst(case: Case) -> AnalystResult | None:
        return AnalystResult(label="legit", confidence=0.95)

    client = TestClient(create_app(Settings(), analyst=mock_analyst))

    response = client.post(
        "/caso", json={"country": "CO", "text": "Gracias por su compra, su pedido va en camino."}
    )

    assert response.status_code == 200
    verdict = Verdict.model_validate(response.json())
    assert verdict.label == "legit"
    assert verdict.degraded is False


def test_post_case_scam_from_a_hard_signal() -> None:
    client = TestClient(create_app(Settings()))

    response = client.post(
        "/caso", json={"country": "CO", "text": "Ingresa a bancolombia-seguro.com ya"}
    )

    assert response.status_code == 200
    verdict = Verdict.model_validate(response.json())
    assert verdict.label == "scam"


def test_post_case_rejects_an_unknown_country() -> None:
    client = TestClient(create_app(Settings()))

    response = client.post("/caso", json={"country": "ZZ", "text": "hola"})

    assert response.status_code == 422


def test_post_case_uses_the_known_campaigns_like_the_measurement() -> None:
    """The endpoint and medir.py judge with the same pack, campaigns included (D-048)."""
    client = TestClient(create_app(Settings()))

    response = client.post("/caso", json={"country": "CO", "text": "Pague hoy en diangov.com"})

    verdict = Verdict.model_validate(response.json())
    assert "known_campaign" in {s.type for s in verdict.signals}
