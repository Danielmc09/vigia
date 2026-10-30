"""Runtime configuration read from the environment (the only place that reads it)."""

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PANEL_TOKEN_MIN = 32
WEBHOOK_CONCURRENCY = 8
"""Webhook messages processed at once; the rest wait their turn (each holds a DB connection)."""


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
        jev_backend: ``real`` asks the Jev judge, a development-only tool (T-08b, D-085);
            ``mock`` (default) never flags. Needs ``VIGIA_MODO=dev`` set explicitly.
        jev_api_key: Jev key; without it the backend is mock whatever ``jev_backend``.
        model_api_key: Meta Model API key; without it the analyst never runs (degraded).
        model_backend: Who answers the chat turns of the analyst and the judge (T-09b):
            ``spark`` (Meta Model API) or ``spark_open`` (Muse Spark Open on Baseten).
        baseten_api_key: Baseten key for ``spark_open``; without it the chat is off.
        voice_max_seconds: Longest voice note transcribed (D-070); longer ones are refused.
        pdf_max_bytes: Largest PDF read by Spark (D-071); bigger ones are refused.
        panel_token: Operator token for ``/panel`` (D-073); unset means no panel at all.
        panel_metrics: Eval result whose metrics the panel shows, if the file exists;
            relative to the working directory (``/app`` in the image, D-072).
        wazenda_base_url: Wazenda public API; media is only downloaded from its exact host.
        wazenda_api_key: ``X-API-Key`` for downloads and sends.
        wazenda_line_id: WhatsApp line VIGIA sends from.
        wazenda_webhook_secret: HMAC key of ``X-Wazenda-Signature``; unset refuses every
            webhook (specs/01).
        metrics_token: Bearer token for ``/metrics`` (specs/11); unset means no endpoint.
        sentry_dsn: Sentry DSN; unset leaves error reporting off (D-084).
        operator_phone: WhatsApp number the sentinel alerts (specs/11 section 4).
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
    model_backend: Literal["spark", "spark_open"] = Field("spark", validation_alias="MODEL_BACKEND")
    baseten_api_key: SecretStr | None = Field(None, validation_alias="BASETEN_API_KEY")
    voice_max_seconds: int = Field(120, ge=1, validation_alias="VIGIA_VOICE_MAX_SECONDS")
    pdf_max_bytes: int = Field(5 * 1024 * 1024, ge=1, validation_alias="VIGIA_PDF_MAX_BYTES")
    panel_token: SecretStr | None = Field(None, validation_alias="VIGIA_PANEL_TOKEN")
    panel_metrics: Path = Field(
        Path("evals/ultimo.resultado.json"), validation_alias="VIGIA_PANEL_METRICS"
    )
    wazenda_base_url: str = Field(
        "https://wazenda.com/api/v1/public", validation_alias="WAZENDA_BASE_URL"
    )
    wazenda_api_key: SecretStr | None = Field(None, validation_alias="WAZENDA_API_KEY")
    wazenda_line_id: str | None = Field(None, validation_alias="WAZENDA_LINE_ID")
    wazenda_webhook_secret: SecretStr | None = Field(
        None, validation_alias="WAZENDA_WEBHOOK_SECRET"
    )

    metrics_token: SecretStr | None = Field(None, validation_alias="METRICS_TOKEN")
    sentry_dsn: SecretStr | None = Field(None, validation_alias="SENTRY_DSN")
    operator_phone: str | None = Field(None, validation_alias="OPERADOR_TELEFONO")

    @field_validator("operator_phone", mode="before")
    @classmethod
    def _operator_phone_e164(cls, raw: str | None) -> str | None:
        """Blank means unset; anything else must be E.164, or the sentinel texts nobody."""
        if not raw:
            return None
        if not re.fullmatch(r"\+[1-9]\d{7,14}", raw):
            raise ValueError("OPERADOR_TELEFONO must be E.164, e.g. +573000000001")
        return raw

    @field_validator("model_api_key", mode="before")
    @classmethod
    def _empty_model_key_is_absent(cls, raw: object) -> object:
        """``MODEL_API_KEY=`` (as .env.example ships it) means rules-only mode (D-087)."""
        return raw or None

    @model_validator(mode="after")
    def _prod_needs_explicit_dsns(self) -> Self:
        """Refuse the local-compose default DSNs in production."""
        missing = {"database_url", "migration_database_url"} - self.model_fields_set
        if self.mode == "prod" and missing:
            names = ", ".join(sorted(name.upper() for name in missing))
            raise ValueError(f"prod requires explicit {names}")
        return self

    @model_validator(mode="after")
    def _wazenda_all_or_nothing(self) -> Self:
        """Refuse a half-configured Wazenda: signed webhooks would all fail later (specs/01)."""
        keys = (self.wazenda_api_key, self.wazenda_line_id, self.wazenda_webhook_secret)
        if len({bool(key) for key in keys}) > 1:  # an empty value counts as unset
            raise ValueError(
                "WAZENDA_API_KEY, WAZENDA_LINE_ID and WAZENDA_WEBHOOK_SECRET go together"
            )
        return self

    @model_validator(mode="after")
    def _jev_real_only_in_dev(self) -> Self:
        """Refuse Jev as the runtime judge (T-08b, D-085): only Meta models run outside dev.

        ``dev`` must be declared: ``mode`` defaults to it, and the image runs without the
        compose file, so the default alone would let Jev reach production.
        """
        if self.jev_backend == "real" and (
            self.mode != "dev" or "mode" not in self.model_fields_set
        ):
            raise ValueError(
                "JEV_BACKEND=real is a development-only tool; it needs VIGIA_MODO=dev declared"
            )
        return self

    @model_validator(mode="after")
    def _tokens_are_long(self) -> Self:
        """Refuse a guessable operator or metrics token (D-073)."""
        tokens = {"VIGIA_PANEL_TOKEN": self.panel_token, "METRICS_TOKEN": self.metrics_token}
        for name, token in tokens.items():
            if token is not None and len(token.get_secret_value()) < PANEL_TOKEN_MIN:
                raise ValueError(f"{name} needs at least {PANEL_TOKEN_MIN} characters")
        return self


settings = Settings()
