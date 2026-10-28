# RUNBOOK

One section per alert (specs/11 §5): symptom → how to confirm → probable cause → action →
how to verify. Every alert reaches the operator on WhatsApp with its name, value,
threshold, `case_id` or `trace_id`, and a link to its section here. A new alert born from an
incident gets its section in the same commit.

Shared tools:

- Health: `curl -s https://<host>/salud` → `{"status": "ok"|"fail", "checks": {...}}`, 503 on any `fail`.
- Metrics: `curl -s -H "Authorization: Bearer $METRICS_TOKEN" https://<host>/metrics | grep vigia_`.
- Logs: JSON lines on stdout (`VIGIA_MODO=prod`); filter one case with `trace_id`, never with phone or text (they are not there).
- Sentinel: `uv run --env-file .env python herramientas/centinela.py` (exit 0 ok, 1 alert, 3 no operator channel).

Detection today: only the sentinel alerts by itself. The SLI alerts of specs/11 §4 (cost,
Meta error streaks, webhook failures, degraded ratio) need state across requests, which
arrives with Redis (layer 2, T-16); until then each section below says how it is noticed
(**Detected today:**). The numbers are in `/metrics` and the logs.

Scheduling: nothing in the repo runs on a timer. The sentinel runs every 15 minutes as a
Railway cron service (T-19) with the same image and env:
`python herramientas/centinela.py --estado /tmp/vigia-sentinel.json`. Test the operator
channel once with `--alerta-prueba`.

## webhook-down

- **Detected today:** by hand (members report no answer, `/salud`). No automatic alert; the sentinel does not go through the webhook.
- **Symptom:** members forward messages and get no answer; `vigia_webhook_total` stops growing or `status="unauthorized"` grows. Alert: 2 failures in 5 minutes.
- **Confirm:** `curl https://<host>/salud`; in Wazenda's dashboard, the webhook delivery log shows errors; `grep webhook_failed` in the logs.
- **Probable cause:** app down or redeploying; tunnel/URL changed; `WAZENDA_WEBHOOK_SECRET` rotated on one side only (every request 401); `WAZENDA_*` half configured (the app refuses to start).
- **Action:** restart the service; re-register `https://<host>/webhook/wazenda` in Wazenda; set the same secret on both sides.
- **Verify:** send a message from a member phone; `vigia_webhook_total{status="accepted"}` grows and the reply arrives.

## meta-errors

- **Detected today:** sentinel (`sentinel_verdict` with value `error` or `sentinel_latency`), within 15 minutes. The streak alert is layer 2.
- **Symptom:** verdicts arrive degraded or late; `vigia_model_calls_total{status="429"}` or `5xx` grows. Alert: 3 in a row.
- **Confirm:** `/salud` → `"meta": "fail"`; `grep stage_failed` with `"stage": "analyst"`.
- **Probable cause:** rate limit, Meta incident, model renamed (the sentinel sees it as `sentinel_verdict` too).
- **Action:** wait out a 429 burst; for 5xx, nothing to do but let the rules answer (degraded); for a renamed model, update the model id in config and the fixtures (T-02).
- **Verify:** `"meta": "ok"` and new cases with `degraded=false`.

## credits-exhausted

- **Detected today:** sentinel, as in [meta-errors](#meta-errors).
- **Symptom:** every Meta call fails with 402/403 or a billing error; all verdicts degraded.
- **Confirm:** Meta developer console → billing; `vigia_model_calls_total` by status.
- **Probable cause:** credits spent or the Spend Limit reached.
- **Action:** Angel tops up or raises the Spend Limit (never Claude Code). Until then the rules keep answering.
- **Verify:** sentinel run exits 0; `degraded=false` again.

## jev-down

- **Detected today:** by hand (`/salud`). The sentinel does not see it: the rules floor keeps the canonical verdicts.
- **Symptom:** `/salud` → `"jev": "fail"` (only when `JEV_BACKEND=real`).
- **Confirm:** `grep stage_failed` with `"stage": "jev"`; latency of the `jev` stage near its 2 s timeout.
- **Probable cause:** TypeSafe incident or key revoked.
- **Action:** set `JEV_BACKEND=mock` and restart: the deterministic manipulation floor keeps working (D-051).
- **Verify:** `"jev": "skip"` (mock) or `"ok"`.

## dlq-not-empty

Layer 2 (queue and Redis, T-16). No queue exists yet; messages run in-process with a
concurrency cap. Section to be written with the queue.

## degraded-high

- **Detected today:** by hand (`/metrics`). The ratio alert is layer 2.
- **Symptom:** more than 10% of cases in 15 minutes come out degraded (`vigia_cases_total{degraded="true"}` over total). Note: cases the rules decide are degraded by design (D-061); compare against the usual ratio, not against zero.
- **Confirm:** `vigia_cases_total` by `degraded`; `vigia_stage_errors_total{stage="analyst"}`.
- **Probable cause:** see [meta-errors](#meta-errors) or [credits-exhausted](#credits-exhausted); `MODEL_API_KEY` missing after a deploy (`/salud` → `"meta": "skip"`).
- **Action:** fix the cause above.
- **Verify:** the ratio is back to its usual value.

## postgres-down

- **Detected today:** by hand (`/salud` 503); the sentinel does not touch Postgres.
- **Symptom:** `/salud` 503 with `"postgres": "fail"`; webhook messages fail (`webhook_failed` in logs).
- **Confirm:** `psql "$DATABASE_URL" -c 'select 1'` from the service shell.
- **Probable cause:** database restarting, credentials rotated, connection limit reached.
- **Action:** restart Postgres; check `DATABASE_URL` and the `vigia_app` role; messages acked during the outage are lost and must be forwarded again (no queue until T-16).
- **Verify:** `"postgres": "ok"` and a member message gets its verdict.

Redis: layer 2 (T-16), not checked yet.

## sentinel-failing

- **Detected today:** automatic (WhatsApp to `OPERADOR_TELEFONO`) once the cron exists (T-19).
- **Symptom:** WhatsApp alert `sentinel_verdict` (a canonical case changed label) or `sentinel_latency` (over 5 s for text, 25 s for screenshot or voice note).
- **Confirm:** run the sentinel by hand and read its JSON lines; find the run in the logs by the `trace_id` of the alert. Canonical cases: `c-0001` (text), `c-0189` (QR screenshot), `c-0145` (voice note) in `evals/casos.jsonl`.
- **Probable cause:** a rule change (run `make evals-rapido`), Meta changed a response format or renamed a model, a reader is missing its key (value `error`), or Meta is slow.
- **Action:** a rule regression is an incident: add the eval first (rule 4), then fix. A Meta change: update the adapter and its fixture. Slowness: see [meta-errors](#meta-errors).
- **Verify:** the sentinel exits 0. The same alert for the same case is not repeated for 15 minutes.

## cost-spike

- **Detected today:** by hand (`/metrics`, Meta console). The budget alert is layer 2.
- **Symptom:** `vigia_usd_total` grows faster than the daily budget (`ALERTA_PRESUPUESTO_USD`) or credits left fall below 20 USD.
- **Confirm:** `/metrics` → `vigia_usd_total` and `vigia_cases_total` by `business_id`; one business or sender dominating is abuse (`vigia_rejected_total` helps).
- **Probable cause:** a member forwarding in a loop, a large PDF or image flood, the sentinel scheduled too often (about 0.01 USD per run).
- **Action:** pause the business (remove its members) or lower the caps; check the sentinel schedule.
- **Verify:** the spend rate is back under budget.
