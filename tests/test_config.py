import pytest
from pydantic import ValidationError

from vigia.config import Settings

_DSN_VARS = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "VIGIA_MODO")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _DSN_VARS:
        monkeypatch.delenv(var, raising=False)


def test_default_mode_is_dev() -> None:
    assert Settings().mode == "dev"


def test_prod_without_explicit_dsns_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(mode="prod")


def test_prod_with_explicit_dsns_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db/vigia")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner@db/vigia")

    assert Settings(mode="prod").mode == "prod"


def test_empty_model_api_key_degrades_like_an_absent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-22c incident: an empty MODEL_API_KEY must degrade like an absent one.

    .env.example ships ``MODEL_API_KEY=`` (empty, not absent) so `make demo` works
    without a real key. Rules only sees an empty string as a real key.
    """
    monkeypatch.setenv("MODEL_API_KEY", "")

    assert Settings().model_api_key is None


def test_empty_mode_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only MODEL_API_KEY treats empty as absent: VIGIA_MODO= must not fall back to dev."""
    monkeypatch.setenv("VIGIA_MODO", "")

    with pytest.raises(ValidationError, match="VIGIA_MODO"):
        Settings()


def test_empty_panel_token_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty VIGIA_PANEL_TOKEN stops startup instead of silently closing the panel."""
    monkeypatch.setenv("VIGIA_PANEL_TOKEN", "")

    with pytest.raises(ValidationError, match="VIGIA_PANEL_TOKEN"):
        Settings()


def test_prod_rejects_the_example_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-22c incident (juez): .env.example keys are public; PII under them is not secret."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db/vigia")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner@db/vigia")
    example = dict(
        line.split("=", 1)
        for line in open(".env.example").read().splitlines()
        if line.startswith(("VIGIA_KEK=", "PII_HMAC_KEY="))
    )
    monkeypatch.setenv("VIGIA_KEK", example["VIGIA_KEK"])

    with pytest.raises(ValidationError, match="example"):
        Settings(mode="prod")
