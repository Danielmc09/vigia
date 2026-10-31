# Privacy

This document is not legal advice. It describes how VIGÍA handles personal data
today, against the laws of the countries its country packs cover, so a business
or a judge can check the claim against the code. See
[THREAT_MODEL.md](THREAT_MODEL.md) for attacker-facing threats and
[`specs/16`](../specs/16-cifrado-de-datos.md) for the full encryption design.

## 1. What is stored, for how long

| Data | Retention target (spec) | Status in this repo |
|---|---|---|
| Messages and verdicts | 30 days | Stored encrypted (`cases.text_enc`, `verdicts.payload_enc`); no automated deletion job yet — see gap below |
| Attachments (image, audio, PDF, .eml) | 7 days | Not persisted at all: decrypted in memory only to be read (Spark/SAM/Voice), then discarded; the case record keeps only the resulting text |
| Campaign fingerprints (simhash, pHash, lookalike domain, account) | Forever | Stored in clear in `known_campaigns` — not PII, anonymous indicators shared across businesses (D-017) |
| Metrics, costs, latencies | Forever | In clear by design, no PII |
| Logs | Operational lifetime of the host | No PII; phone numbers are masked to a short prefix before they ever reach stdout (`telemetry.mask_phone`) |

**Gap, stated plainly:** `specs/16` §4 calls for a daily job
(`herramientas/retencion.py`) that physically deletes rows past their retention
window. That job is not built yet. Retention today is a target, not an enforced
behavior, and this document does not claim otherwise.

## 2. Where it runs

VIGÍA has no hosted instance (D-112). Everything — Postgres, the API, the
analyst — runs locally, on the machine of whoever runs `make demo` (Docker
Compose + a local Postgres container). There is no cloud region to name: the
original plan to host on Railway was discarded (D-112) after a Meta model-API
entitlements bug made the model unreachable from that host, and Cerebral
Valley confirmed judges do not need a hosted version. If VIGÍA is deployed in
the future, the region and host are chosen by whoever deploys it, and `/caso`
must be put behind a token first (see THREAT_MODEL.md §4).

## 3. Who can access what

- **Row-level security by business.** Every tenant table (`businesses`,
  `cases`, …) has `business_id`, `ENABLE` + `FORCE ROW LEVEL SECURITY`, and a
  policy keyed on `current_setting('app.business_id')`. The API connects as
  `vigia_app`, a role with no superuser and no `BYPASSRLS`. A business can
  never see another business's rows through the API (`tests/test_owasp.py`
  A01 tests).
- **The operator**, the only role holding `VIGIA_PANEL_TOKEN`, can see every
  business's cases and signal taxonomy through the panel (D-073, D-111) — this
  is a deliberate, scoped exception for debugging and for showing judges the
  evidence behind a verdict, not a default. No separate audit log of operator
  panel views exists yet in the repo; auditing today is "Angel is the only
  person with the token."
- **Cross-business campaign knowledge** exposes only `known: true/false` and
  `public_source` to a business's own API responses and WhatsApp replies —
  never another business's text, who reported it, or when (D-017,
  `tests/test_owasp.py` A01 tests cover the isolation boundary this depends
  on).

## 4. How it's deleted

`store.postgres.delete_business(conn, business_id)` deletes a business's row;
`cases`/`verdicts`/`members` reference it with `ON DELETE CASCADE`, and the
business's DEK (data encryption key) is deleted with it. Because every PII
column is encrypted under that DEK, this is crypto-shredding: once the DEK is
gone, the ciphertext that is left (in the database or in any backup) is
unrecoverable, even though the bytes briefly still exist on disk until a
backup cycles out (`tests/test_cifrado.py::test_deleting_a_business_shreds_its_key`).

What is **not** built yet: an owner-facing, two-step "delete business"
WhatsApp command (type it, get asked to confirm, then it runs). Today deletion
is a store function an operator calls directly. This is a real gap against
`specs/16` §4's "`eliminar negocio` en 2 pasos," named here rather than claimed
as done (AGENTS.md rule 9).

Anonymous campaign fingerprints (simhash, pHash, domain, account) are **not**
deleted when a business is deleted — they carry no business identity once
written (only a hashed `first_seen_business_id` for the operator's own
debugging), and keeping them is what protects every other business from the
same campaign (D-017).

## 5. Encryption at rest and search

Full design in [`specs/16`](../specs/16-cifrado-de-datos.md): envelope
encryption (KEK wraps a per-business DEK), AES-256-GCM with the `cryptography`
library for anything read back, keyed HMAC-SHA256 for anything only searched
(phone, email — `WHERE phone_hash = hmac(value)`, never `LIKE`). Verified by
`tests/test_cifrado.py`: a raw `SELECT` on any table finds no PII in clear,
and a business's DEK cannot decrypt another business's or another column's
ciphertext.

## 6. Laws by country pack

Not legal advice — a map from what each law broadly requires to what VIGÍA
does about it today, so a gap is visible instead of implied.

| Country / law | What it broadly requires | How VIGÍA complies | What's missing |
|---|---|---|---|
| Colombia — Ley 1581 de 2012 (Habeas Data) | Informed consent to process personal data, purpose limitation, right to access/correct/delete one's data | Enrollment is opt-in by the business (D-002-class flows); data is encrypted per business; deletion exists as crypto-shredding | No explicit consent text shown at enrollment yet; no owner-facing data-access/export command; no two-step delete command (see §4) |
| Brazil — LGPD | Similar to GDPR: legal basis, data subject rights, breach notification | Encryption at rest and per-tenant isolation (RLS) support "security measures" requirements | No breach-notification process documented; no Portuguese country pack yet, so no Brazil deployment exists to comply for |
| Mexico — LFPDPPP | Privacy notice (aviso de privacidad), consent, ARCO rights (access, rectify, cancel, oppose) | Same technical controls as above | No privacy notice text; no Mexico country pack yet |
| United States — sectoral + state laws (e.g. CCPA/CPRA in California) | Varies by sector/state; commonly a right to know/delete, opt-out of sale (VIGÍA does not sell data, so this is moot) | US country pack exists for scam patterns; same encryption/RLS controls apply | No state-specific disclosures; no documented data-sale posture statement (there is none to disclose — VIGÍA does not sell data) |
| European Union — GDPR (only if a business based in the EU ever enrolls) | Lawful basis, data minimization, right to erasure, DPA with processors | Data minimization is a running design principle (D-064: address-book disclosure minimization; disarmed quotes instead of raw attacker text, D-108); erasure exists as crypto-shredding | No Data Processing Agreement text with Meta/Wazenda; no EU country pack; GDPR does not currently apply because no EU deployment or EU business is enrolled |

## 7. Providers

- **Meta Model API**, tier **Standard**: per Meta's own terms, Standard-tier
  calls are not used to train Meta's models. This is the only tier VIGÍA is
  built to use (AGENTS.md rule 10).
- **Jev (TypeSafe)**: a development-only auxiliary judge. `Settings` refuses
  `JEV_BACKEND=real` unless `VIGIA_MODO=dev` is explicitly declared (D-085);
  at runtime (`eval`/`prod`) the gate's model fallback is always Spark, never
  Jev, and any Jev failure falls back to the deterministic mock
  (`tests/test_jev.py::test_any_jev_failure_falls_back_to_mock`).
  Jev never sees or stores message content beyond what a single classification
  call needs.
- **Wazenda**: the WhatsApp Business API processor. Inbound webhooks are
  signature-checked (HMAC) before anything is accepted
  (`tests/test_wazenda.py`); Wazenda is a processor of message content on
  VIGÍA's behalf, not an independent controller.

## 8. Responsible disclosure

See [`SECURITY.md`](../SECURITY.md) for how to report a vulnerability and the
response-time commitment.
