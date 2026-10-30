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
