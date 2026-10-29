# VIGÍA

*Scam sentinel for small businesses, built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026).*

See `DECLARACION.md` for what was built during the event. Full README written in T-22b.

## Meta model calls vs code

At runtime (`VIGIA_MODO=eval|prod`) every model call is a Meta model: Muse Spark 1.3
(analyst loop, attachment reading, and the gate's fallback judge), SAM 3.1 (crops), Muse
Voice Transcribe (voice notes). Everything else behind a "model" name is deterministic
code: the country-pack signals, the manipulation floor (D-051), fusion and policy.

TypeSafe Jev is a **development-only** tool (judge of steps), never called at runtime:
`Settings` refuses `JEV_BACKEND=real` outside `VIGIA_MODO=dev` (T-08b, D-085). The gate
(`jev/gate.py`) always runs the deterministic floor first; its model fallback is Spark in
`eval`/`prod` and, only in `dev`, optionally Jev.
