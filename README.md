# VIGÍA

*Scam sentinel for small businesses, built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026).*

## Run the demo

Needs Docker (with Compose) and [uv](https://docs.astral.sh/uv/).

```
git clone <this repo> && cd vigia
cp .env.example .env   # MODEL_API_KEY is optional; without it the demo runs rules only
make demo
```

Starts Postgres and the API with Docker Compose, seeds a sample business with 3
members, sends 5 sample cases through the local channel, and opens the panel with the
verdicts. Without `MODEL_API_KEY` it says so and runs rules only (no analyst). The
`.env.example` keys are public demo keys: `VIGIA_MODO=prod` refuses them.

See `DECLARACION.md` for what was built during the event. Full README written in T-22b.

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
