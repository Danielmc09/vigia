"""Runtime configuration read from the environment (the only place that reads it)."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings. Defaults match the local docker compose stack.

    Attributes:
        database_url: DSN for the application role (no superuser, no BYPASSRLS).
        migration_database_url: DSN for the schema owner; used only by the migration runner.
        mode: Deployment mode. API docs are disabled in ``prod``.
        kek: Base64 key-encryption key that wraps each business DEK (specs/16).
        pii_hmac_key: Base64 key for the lookup hashes of phones and emails (specs/16).
        email_codes_per_hour: Verification emails one address may receive per hour (D-028).
        email_codes_per_business_hour: Verification emails one business may trigger per hour.
        code_max_attempts: Wrong guesses that invalidate a member's live codes (specs/12 B4).
        knowledge_seed: Known-campaign seed attached to the country packs (D-048).
        jev_backend: ``real`` asks TypeSafe Jev; ``mock`` (default) never flags (T-08).
        jev_api_key: TypeSafe key; without it the backend is mock whatever ``jev_backend``.
        model_api_key: Meta Model API key; without it the analyst never runs (degraded).
    """

    model_config = SettingsConfigDict(populate_by_name=True)

    database_url: str = "postgresql://vigia_app:vigia_app@localhost:5432/vigia"
    migration_database_url: str = "postgresql://postgres:postgres@localhost:5432/vigia"
    mode: Literal["dev", "eval", "prod"] = Field("dev", validation_alias="VIGIA_MODO")
    kek: SecretStr | None = Field(None, validation_alias="VIGIA_KEK")
    pii_hmac_key: SecretStr | None = Field(None, validation_alias="PII_HMAC_KEY")
    email_codes_per_hour: int = Field(3, ge=1, validation_alias="VIGIA_EMAIL_CODES_PER_HOUR")
    email_codes_per_business_hour: int = Field(
        10, ge=1, validation_alias="VIGIA_EMAIL_CODES_PER_BUSINESS_HOUR"
    )
    code_max_attempts: int = Field(3, ge=1, validation_alias="VIGIA_CODE_MAX_ATTEMPTS")
    knowledge_seed: Path = Field(
        Path("conocimiento/semilla.jsonl"), validation_alias="VIGIA_KNOWLEDGE_SEED"
    )

    jev_backend: Literal["mock", "real"] = Field("mock", validation_alias="JEV_BACKEND")
    jev_api_key: SecretStr | None = Field(None, validation_alias="JEV_API_KEY")
    model_api_key: SecretStr | None = Field(None, validation_alias="MODEL_API_KEY")

    @model_validator(mode="after")
    def _prod_needs_explicit_dsns(self) -> Self:
        """Refuse the local-compose default DSNs in production."""
        missing = {"database_url", "migration_database_url"} - self.model_fields_set
        if self.mode == "prod" and missing:
            names = ", ".join(sorted(name.upper() for name in missing))
            raise ValueError(f"prod requires explicit {names}")
        return self


settings = Settings()
