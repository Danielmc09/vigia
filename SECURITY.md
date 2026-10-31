# Security policy

VIGÍA is a hackathon project built by one person (Angel). There is no bug
bounty and no dedicated security team, but reports are taken seriously and
acknowledged quickly.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting (the repo's **Security** tab →
**Report a vulnerability**) with a description of the issue, the steps
to reproduce it, and, if you have one, the smallest proof of concept that
shows the problem. Do not open a public GitHub issue for a vulnerability that
has not been fixed yet — this repo is public from its first commit (AGENTS.md
rule 18), so an unfixed report filed in public is itself a disclosure.

## What's in scope

- The API in `src/vigia` (signal detection, verdict/policy logic, the operator
  panel, the WhatsApp/email channel adapters).
- The data model and its encryption (`specs/16`, `src/vigia/store`).
- The import contracts and multi-tenancy isolation (`docs/ARCHITECTURE.md`).

## What's out of scope

- Findings that require physical access to a machine running `make demo`.
- Findings against Meta's, Wazenda's, or any other third-party provider's own
  infrastructure — report those to the provider directly.
- Volumetric denial of service against a demo instance you spun up yourself.

## Response time

- **Acknowledgment within 3 business days** of the report.
- **A triage verdict (confirmed / not a vulnerability / needs more info)
  within 10 business days.**
- A confirmed vulnerability gets a fix or a documented mitigation before the
  next tagged state of the repo; there is no fixed SLA on the fix itself given
  the one-person team, but the reporter is kept updated on progress.

## Known, documented gaps

Rather than leaving a security reader to guess, the current, deliberately
scoped gaps are named in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) §4
("What it does NOT cover") — including that `/caso` is public and tokenless
while there is no hosted instance, and that the daily retention-deletion job
does not exist yet. A report about something already listed there is still
welcome; it helps prioritize which gap gets closed first.
