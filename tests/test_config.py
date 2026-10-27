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
