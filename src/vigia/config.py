"""Runtime configuration read from the environment (the only place that reads it)."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PANEL_TOKEN_MIN = 32


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
        voice_max_seconds: Longest voice note transcribed (D-070); longer ones are refused.
        pdf_max_bytes: Largest PDF read by Spark (D-071); bigger ones are refused.
        panel_token: Operator token for ``/panel`` (D-073); unset means no panel at all.
        panel_metrics: Eval result whose metrics the panel shows, if the file exists;
            relative to the working directory (``/app`` in the image, D-072).
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
    voice_max_seconds: int = Field(120, ge=1, validation_alias="VIGIA_VOICE_MAX_SECONDS")
    pdf_max_bytes: int = Field(5 * 1024 * 1024, ge=1, validation_alias="VIGIA_PDF_MAX_BYTES")
    panel_token: SecretStr | None = Field(None, validation_alias="VIGIA_PANEL_TOKEN")
    panel_metrics: Path = Field(
        Path("evals/ultimo.resultado.json"), validation_alias="VIGIA_PANEL_METRICS"
    )

    @model_validator(mode="after")
    def _prod_needs_explicit_dsns(self) -> Self:
        """Refuse the local-compose default DSNs in production."""
        missing = {"database_url", "migration_database_url"} - self.model_fields_set
        if self.mode == "prod" and missing:
            names = ", ".join(sorted(name.upper() for name in missing))
            raise ValueError(f"prod requires explicit {names}")
        return self

    @model_validator(mode="after")
    def _panel_token_is_long(self) -> Self:
        """Refuse a guessable operator token (D-073)."""
        token = self.panel_token
        if token is not None and len(token.get_secret_value()) < PANEL_TOKEN_MIN:
            raise ValueError(f"VIGIA_PANEL_TOKEN needs at least {PANEL_TOKEN_MIN} characters")
        return self


settings = Settings()
