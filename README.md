# VIGÍA

*Scam sentinel for small businesses, built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026).*

VIGÍA is the WhatsApp number and email address a small business forwards
anything suspicious to — a text, a screenshot, a voice note, a PDF, a link, an
email — and gets back a verdict in plain language in seconds: **scam /
legitimate / doubtful**, why, and what to do. If it's a scam, the rest of the
team is warned too.

**GIF: pending, T-22 will record it.**

## Metrics (last dated run)

No clean final number exists yet; this is the latest dated run, with its
caveat, never invented:

| Date / commit | Cases | Scam recall | False alarms on legit | Doubtful | Cost | Note |
|---|---|---|---|---|---|---|
| 2026-10-31, `859c500` | 205 | 98.2% | 2.3% | 8.3% | $0.750 | The automated gate flagged a regression in accuracy and in doubtful rate on this run — not a clean passing number, see `ESTADO.md` |

Target (`OBJETIVOS.md`, ≥80 cases): `recall_estafa ≥ 0.90`, `fp_legitimo ≤
0.10`, `dudosos ≤ 0.25`.

## Run the demo

Needs Docker (with Compose) and [uv](https://docs.astral.sh/uv/).

```
git clone <this repo> && cd vigia
cp .env.example .env   # MODEL_API_KEY is optional; without it the demo runs rules only
make demo
```

Starts Postgres and the API with Docker Compose, seeds a sample business with 3
members, sends 5 sample cases through the local channel, and opens the panel with the
verdicts at `http://localhost:8000/panel?token=<VIGIA_PANEL_TOKEN>` (it prints the full
link). There is no hosted instance: `make demo` on your machine is the way to try it.
The link sets an HttpOnly cookie and redirects to `/panel`, so the token leaves the address
bar. Escape the token if it has `+`, `/` or `=`. Without `MODEL_API_KEY` it says so
and runs rules only (no analyst). The `.env.example` keys are public demo keys:
`VIGIA_MODO=prod` refuses them.

See `DECLARACION.md` for what was built during the event.

## Meta model calls vs code

At runtime (`VIGIA_MODO=eval|prod`) every model call is a Meta model: Muse Spark 1.3
(analyst loop, attachment reading, and the gate's fallback judge), SAM 3.1 (crops), Muse
Voice Transcribe (voice notes). Everything else behind a "model" name is deterministic
code: the country-pack signals, the manipulation floor (D-051), fusion and policy.

TypeSafe Jev is a **development-only** tool (judge of steps), never called at runtime:
`Settings` refuses `JEV_BACKEND=real` unless `VIGIA_MODO=dev` is declared explicitly
(the default mode does not count; the Docker image sets `VIGIA_MODO=prod`; T-08b, D-085). The gate
(`jev/gate.py`) always runs the deterministic floor first; its model fallback is Spark in
`eval`/`prod` and, only in `dev`, optionally Jev.

## What each Meta model does

| Model | Does |
|---|---|
| Muse Spark 1.3 | The analyst's tool-using loop (web search, pre-resolved link lookup), reading screenshots/PDFs when rules alone aren't enough, and the gate's fallback judge for manipulation scoring at runtime |
| SAM 3.1 | Crops logos and QR codes out of a screenshot; the QR is then decoded in code, not by the model |
| Muse Voice Transcribe | Turns a WhatsApp voice note into text, in code, before any signal or model ever sees it |

## Rubric (`OBJETIVOS.md`, Objetivo 2 — maximize the judges' score)

| Criterio | Peso | Lo que lo gana |
|---|---|---|
| Impacto | 30% | Problema con cifras + pyme real en el video + onboarding sin app |
| Meta tooling | 25% | Spark como agente orquestando SAM y Voice; QR recortado; audio; Glimmer si se logra |
| Demo | 25% | Nota de voz abre el video; alerta al equipo; correo BEC; legítimo en 2 s |
| Técnica | 20% | medir.py + gate, preregistro, adversarios, RLS, 3 capas |

Bonus objetivo: Agentic (5k) y Open Weights (10k) por jueces; Community Vote
(10k) por LinkedIn y grupos. Most Viral (X) no es objetivo.

## Security

OWASP categories this repo tests for, mapped to the test that covers them:

| OWASP category | Covered by |
|---|---|
| A01 — Broken access control | `tests/test_owasp.py` (`test_a01_business_sees_only_its_own_rows`, `test_a01_without_business_context_no_rows_are_visible`, `test_a01_cannot_write_rows_for_another_business`) |
| A03 — Injection | `tests/test_owasp.py` (`test_a03_detector_flags_runtime_built_queries`, `test_a03_no_runtime_built_sql_in_src`, `test_a03_business_id_is_bound_not_interpolated`) |
| A05 — Security misconfiguration | `tests/test_owasp.py` (`test_a05_security_headers_are_set`, `test_a05_no_api_docs_in_prod`, `test_a05_public_texts_never_name_internal_signals`) |
| A09 — Logging and monitoring failures | `tests/test_owasp.py` (`test_a09_case_logs_carry_ids_but_no_pii`, `test_a09_uvicorn_errors_are_json_without_pii`) |
| A10 — Server-side request forgery | `tests/test_net.py`, over `src/vigia/net/unroll.py` (D-099): short-link unrolling refuses non-public addresses, caps hops and time, never forwards cookies |

Full write-up: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) and
[`docs/PRIVACY.md`](docs/PRIVACY.md); encryption design in
[`specs/16`](specs/16-cifrado-de-datos.md); how to report a vulnerability in
[`SECURITY.md`](SECURITY.md).

## Out of scope

Lo que NO es objetivo de esta hackathon: monetizar, multi-país completo, MCP
para terceros, auditar chatbots ajenos, cubrir zero-click. Todo eso vive en
"Ideas para después."

## Benchmark

Pending, see `specs/14` §2 (VIGÍA-Bench, published separately once built).

## More

- [`docs/MAP.md`](docs/MAP.md) — one line per module, its layer and
  responsibility.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — component and flow
  diagrams, import contracts, multi-tenancy.
- [`docs/api/`](docs/api/) — generated API reference (`pdoc`).
- [`docs/decisions/`](docs/decisions/) — every technical decision (ADR-001
  through ADR-112), migrated to English from `DECISIONES.md`.
