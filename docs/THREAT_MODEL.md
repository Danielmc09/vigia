# Threat model

VIGÍA is a WhatsApp/email scam sentinel for small businesses. This document names
who can attack it, what stops them today, and what proves it. It is not legal
advice (see [PRIVACY.md](PRIVACY.md) for the laws table and its disclaimer).

## 1. Threats

| Threat | Surface | Control | Evidence |
|---|---|---|---|
| A scammer sends text engineered to make the system itself answer "legitimate" (prompt injection), or floods it with repeats to burn model spend | Inbound WhatsApp message / `/caso` | Deterministic manipulation floor runs before any model call and cannot be out-argued by the message text (specs/08, D-051); a forward under 6 hours old repeats the stored verdict instead of re-analyzing (D-106); webhook signature + idempotency by message id and media hash cap duplicate processing (D-080, D-083) | `tests/test_jev.py` (`test_injection_phrases_are_flagged_by_rules_even_with_jev_down`, `test_every_adversarial_injection_is_flagged_by_rules`, `test_case_endpoint_never_returns_legit_for_an_injection`); `tests/test_webhook.py::test_webhook_events_remember_a_message_id_hash` |
| An enrolled employee misuses the channel (spams commands, tries owner-only actions, or probes for how the system decides) | WhatsApp commands from an enrolled number | Command parser replies with a fixed usage string instead of acting on malformed input (D-092); escalations (irreversible actions) require the owner's `confirmar`, never an employee's (specs/12 B7); signal names never appear in text sent to WhatsApp or the team alert (D-111) | `tests/test_inscripcion.py`; `tests/test_owasp.py::test_a05_public_texts_never_name_internal_signals` |
| A business owner tries to see another business's cases, members, or campaign history | Panel, `/caso`, WhatsApp replies | Postgres row-level security scoped by `business_id`, enforced for the non-superuser `vigia_app` role (`FORCE ROW LEVEL SECURITY`); cross-business campaign knowledge exposes only an anonymous indicator match (`known: true/false`, `public_source`), never the other business's data (D-017) | `tests/test_owasp.py` (`test_a01_business_sees_only_its_own_rows`, `test_a01_without_business_context_no_rows_are_visible`, `test_a01_cannot_write_rows_for_another_business`) |
| The operator (the only role that holds `VIGIA_PANEL_TOKEN`) is compromised, or an attacker guesses/steals the panel token | Operator panel (`/panel`, `/panel.js`) | Panel requires a configured, non-empty token; it is exchanged for an HttpOnly, SameSite=Lax cookie so it never sits in logs or browser history after the first load (D-073, D-088); wrong or missing tokens are refused; the panel is the only place signal taxonomy is shown, by design (D-111) — compromising it exposes taxonomy and case metadata, never raw PII in clear (storage is still encrypted) | `tests/test_panel.py` (`test_panel_does_not_exist_without_a_configured_token`, `test_panel_refuses_a_missing_or_wrong_token`, `test_a_browser_link_trades_the_token_for_a_cookie_and_drops_it_from_the_url`) |
| A provider (Meta Model API, Jev, Wazenda) goes down, is substituted, or misbehaves | Model calls, webhook delivery | The gate's deterministic floor runs first and alone is enough to classify in `legitimate`/`suspicious`/`scam`-obvious cases; Spark is the runtime judge fallback, Jev is development-only and `Settings` refuses `JEV_BACKEND=real` outside `VIGIA_MODO=dev` (D-085); a Spark content filter or outage degrades to a documented degraded verdict rather than guessing (D-086); Wazenda webhooks are signature-checked and rejected without a valid HMAC (D-080) | `tests/test_jev.py::test_backend_is_mock_unless_real_and_keyed`; `tests/test_wazenda.py` (`test_a_missing_or_wrong_signature_is_refused`, `test_a_body_changed_after_signing_is_refused`, `test_without_a_secret_nothing_is_accepted`) |
| The database is leaked (backup theft, compromised credentials, a raw dump) | Postgres | Every PII column is either AES-256-GCM encrypted per business (envelope: KEK wraps a per-business DEK) or a keyed HMAC-SHA256 hash for the fields that only need to be searched; a ciphertext is bound by AAD to its own business and column, so a stolen DEK from one business cannot open another business's rows or a different column's data (specs/16) | `tests/test_cifrado.py` (`test_raw_select_as_superuser_finds_no_pii_in_any_table`, `test_ciphertext_does_not_open_under_another_business`, `test_ciphertext_does_not_open_in_another_column`, `test_deleting_a_business_shreds_its_key`) |

## 2. Encryption at rest and search

See [`specs/16`](../specs/16-cifrado-de-datos.md) for the full field classification,
key hierarchy (KEK/DEK envelope encryption, `PII_HMAC_KEY`), and library choice
(`cryptography`, AESGCM). Summary: anything read back (names, message text,
verdicts) is encrypted per business; anything only compared (phone, email) is a
keyed hash, never `LIKE` over plaintext.

## 3. Data: retention, location, access, deletion

See [PRIVACY.md](PRIVACY.md) for the full retention table, where the system runs,
who can access what, and how a business is deleted.

## 4. What it does NOT cover

- **`/caso` is public and tokenless (D-111).** It returns the structured `Verdict`
  JSON, including `signals[].type` (the internal signal taxonomy). This is
  acceptable **only** while there is no reachable hosted instance (D-112): today
  it lives solely on the machine of whoever runs `make demo`. If VIGÍA is ever
  deployed on a host reachable from the internet, `/caso` **must** go behind a
  token the same way the operator panel does, before the port opens, because:
  1. it exposes the signal taxonomy in the response body;
  2. without a token, anyone can spend the gate judge's model calls (`SparkJudge`
     outside `dev`, per `services/judge.py`) for free, with no cost cap on the
     caller's side.
- **No automated retention/deletion job yet.** `specs/16` §4 calls for a daily
  job that physically deletes messages/verdicts after 30 days and
  attachments/audio after 7; that job does not exist in the repo yet
  (`src/vigia/store/postgres.delete_business` exists and is tested, but there is
  no scheduled retention sweep, and no owner-facing two-step "delete business"
  WhatsApp command — only the store function an operator can call directly).
  Declining to claim this as built rather than inventing it (AGENTS.md rule 9).
- **No rate limiting beyond webhook idempotency and the manipulation floor.** A
  flood of distinct, non-repeating messages from an enrolled number is not
  throttled; it is each analyzed independently (and billed independently).
- **No anomaly detection on operator behavior.** A compromised operator token is
  detected by Angel noticing, not by the system.
- **Attachments are never written to disk unencrypted and are not persisted at
  all today** (processed in memory, the result goes into `cases.text_enc`); this
  means there is currently nothing to separately retention-sweep at 7 days for
  attachments — the whole case record ages with the message instead.
