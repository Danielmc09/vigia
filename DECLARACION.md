# DECLARACION.md — what was built during the hackathon (source for README "Built during the event")

Building period: Oct 27 – Nov 4, 2026. First commit of this repository: Oct 27, 2026. All design documents (`specs/`) were written Oct 26 as planning; **no code existed before Oct 27**.

## Built during the hackathon (original work)
- Everything under `src/vigia/`: channels (WhatsApp adapter, email adapter, local), ingestion, deterministic signals (URLs, QR, patterns, email headers, supplier book, country packs), Jev gate, analyst tool loop and prompts, fusion, policy, storage with RLS, panel, API, observability, sentinel.
- `tools/` and `herramientas/` (repo scripts: case validator, repo map, architecture-doc check).
- `medir.py`, `evals/` (cases, adversarial suite, blind suite), `bench/`.
- `vigia_mcp/`, Hermes profile (`analista/`), country packs `CO.json`, `US.json`, i18n files.
- All docs: README, ARCHITECTURE, MAP, THREAT_MODEL, PRIVACY, decisions, runbook.

Country packs contain only public data (brand names, official domains and service lines) taken from each brand's official site; every brand records its `fuente_url`. Eval cases are synthetic, except the ones marked `kb_test: true`, which quote a known-campaign seed entry to test the knowledge base.

## Third-party content (public, quoted with source)
- `conocimiento/semilla.jsonl`: known scam campaigns. Message texts and malicious domains are quoted or summarized from public alerts and press reports (official agencies, banks, news outlets); every entry records its `source_url`, and `conocimiento/fuentes.json` lists them all. The fingerprints computed from them are ours. No personal data.

## Third-party services used as-is (not built by us)
- Meta Model API (Muse Spark, SAM 3.1, Muse Voice Transcribe), Muse Glimmer open weights. Standard tier.
- Wazenda: WhatsApp Business transport (webhook in / message out). Only the adapter is ours.
- TypeSafe Jev: typed judgments. Only the gate logic is ours.
- Hermes Agent (Nous Research): agent runtime for the optional local-model profile.
- Railway, Postgres, Redis, Resend (inbound email), Sentry.

## Open-source libraries
FastAPI, uvicorn, pydantic, pydantic-settings, asyncpg, cryptography, httpx, structlog, prometheus_client, OpenTelemetry, tldextract, pyzbar/OpenCV, pytest, ruff, mypy, pylint, import-linter, bandit, pip-audit, pdoc, interrogate, git-cliff. Licenses respected; see `pyproject.toml`.

## Not included
No code, data, or assets from any prior project of the author. All sample data is synthetic or anonymized, except the public third-party content listed above. simhash and pHash are implemented in `src/vigia/domain/knowledge.py` with the standard library (no dependency). Test phone numbers (+57 300 000 01xx–03xx, +1 202 555 01xx) are made up and never dialed or messaged: tests send nothing to any number, and outbound email in tests goes to an in-memory recorder.
