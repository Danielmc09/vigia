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
- `evals/muestras/c-0145.wav` … `c-0154.wav`: synthetic voice notes generated with the macOS text-to-speech voices (Apple: Paulina, Mónica, Eddy, Samantha) reading scripts we wrote. The voices are Apple's; the scripts are ours. No real recordings.
- `src/vigia/domain/tlds-iana.txt`: the IANA list of top-level domains, copied as-is from https://data.iana.org/TLD/tlds-alpha-by-domain.txt (version 2026103100, downloaded 2026-10-31; source in the file header). Public data, never fetched at runtime.
- `conocimiento/semilla.jsonl`: known scam campaigns. Message texts and malicious domains are quoted or summarized from public alerts and press reports (official agencies, banks, news outlets); every entry records its `source_url`, and `conocimiento/fuentes.json` lists them all. The fingerprints computed from them are ours. No personal data.

## Third-party services used as-is (not built by us)
- Meta Model API (Muse Spark, SAM 3.1, Muse Voice Transcribe), Muse Glimmer open weights. Standard tier.
- Wazenda: WhatsApp Business transport (webhook in / message out). Only the adapter is ours.
- TypeSafe Jev: typed judgments. **Development tool only** (T-08b, D-085): every model called at runtime is a Meta model; Jev is not one, so it never runs in `eval`/`prod`, and in `dev` only when `VIGIA_MODO=dev` is declared explicitly (`model/jev.py`, enforced by `Settings`; the Docker image sets `VIGIA_MODO=prod`). Only the gate logic is ours.
- Hermes Agent (Nous Research): agent runtime for the optional local-model profile.
- Railway, Postgres, Redis, Resend (inbound email), Sentry.

## Open-source libraries
FastAPI, uvicorn, pydantic, pydantic-settings, asyncpg, cryptography, httpx, structlog, prometheus_client, sentry-sdk, tldextract, zxing-cpp (QR decoding), Pillow (image decoding, crops and resizing), PyAV (WhatsApp ogg/opus voice notes to WAV; its wheels bundle FFmpeg, LGPL), pytest, ruff, mypy, pylint, import-linter, bandit, pip-audit, pdoc, interrogate, git-cliff. Licenses respected; see `pyproject.toml`.

## Not included
No code, data, or assets from any prior project of the author. All sample data is synthetic or anonymized, except the public third-party content listed above. simhash and pHash are implemented in `src/vigia/domain/knowledge.py`: simhash with the standard library, pHash with the standard library over a grid that Pillow decodes and resizes. Test phone numbers (+57 300 000 0001, +57 300 000 01xx–07xx, +1 202 555 01xx) are made up and never dialed or messaged: tests send nothing to any number, and outbound email in tests goes to an in-memory recorder.
