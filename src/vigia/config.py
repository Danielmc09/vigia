"""Runtime configuration read from the environment (the only place that reads it)."""

from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings. Defaults match the local docker compose stack.

    Attributes:
        database_url: DSN for the application role (no superuser, no BYPASSRLS).
        migration_database_url: DSN for the schema owner; used only by the migration runner.
        mode: Deployment mode. API docs are disabled in ``prod``.
    """

    model_config = SettingsConfigDict(populate_by_name=True)

    database_url: str = "postgresql://vigia_app:vigia_app@localhost:5432/vigia"
    migration_database_url: str = "postgresql://postgres:postgres@localhost:5432/vigia"
    mode: Literal["dev", "eval", "prod"] = Field("dev", validation_alias="VIGIA_MODO")

    @model_validator(mode="after")
    def _prod_needs_explicit_dsns(self) -> Self:
        """Refuse the local-compose default DSNs in production."""
        missing = {"database_url", "migration_database_url"} - self.model_fields_set
        if self.mode == "prod" and missing:
            names = ", ".join(sorted(name.upper() for name in missing))
            raise ValueError(f"prod requires explicit {names}")
        return self


settings = Settings()
