# DECLARACION.md — what was built during the hackathon (source for README "Built during the event")

Building period: Oct 27 – Nov 4, 2026. First commit of this repository: Oct 27, 2026. All design documents (`specs/`) were written Oct 26 as planning; **no code existed before Oct 27**.

## Built during the hackathon (original work)
- Everything under `src/vigia/`: channels (WhatsApp adapter, email adapter, local), ingestion, deterministic signals (URLs, QR, patterns, email headers, supplier book, country packs), Jev gate, analyst tool loop and prompts, fusion, policy, storage with RLS, panel, API, observability, sentinel.
- `medir.py`, `evals/` (cases, adversarial suite, blind suite), `bench/`.
- `vigia_mcp/`, Hermes profile (`analista/`), country packs `CO.json`, `US.json`, i18n files.
- All docs: README, ARCHITECTURE, MAP, THREAT_MODEL, PRIVACY, decisions, runbook.

## Third-party services used as-is (not built by us)
- Meta Model API (Muse Spark, SAM 3.1, Muse Voice Transcribe), Muse Glimmer open weights. Standard tier.
- Wazenda: WhatsApp Business transport (webhook in / message out). Only the adapter is ours.
- TypeSafe Jev: typed judgments. Only the gate logic is ours.
- Hermes Agent (Nous Research): agent runtime for the optional local-model profile.
- Railway, Postgres, Redis, Resend (inbound email), Sentry.

## Open-source libraries
FastAPI, pydantic, SQLAlchemy, httpx, structlog, prometheus_client, OpenTelemetry, tldextract, pyzbar/OpenCV, imagehash, simhash, pytest, ruff, import-linter, bandit, pip-audit, pdoc, interrogate. Licenses respected; see `pyproject.toml`.

## Not included
No code, data, or assets from any prior project of the author. All sample data is synthetic or anonymized.
