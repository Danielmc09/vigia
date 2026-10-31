# Architecture

VIGIA is one FastAPI service (`vigia.app`) backed by Postgres with row-level security per
business. This document grows with the code; `tools/check_architecture_doc.py` fails the
build if a top-level package under `src/vigia` is missing from the diagrams below.

## Components

Legend: solid arrow (`-->`) = import that exists today; dotted arrow (`-.->`) = target
dependency, allowed by the import contracts but not written yet.

```mermaid
graph TD
  subgraph application
    vigia.app
    vigia.services
  end
  subgraph rules
    vigia.signals
    vigia.verdict
    vigia.policy
    vigia.jev
  end
  subgraph adapters
    vigia.channel
    vigia.analyst
    vigia.model
    vigia.store
    vigia.net
  end
  subgraph domain
    vigia.domain
  end
  subgraph support
    vigia.config
    vigia.i18n
    vigia.telemetry
  end
  vigia.app --> vigia.config
  vigia.services --> vigia.telemetry
  vigia.telemetry --> vigia.domain
  vigia.store.migrate --> vigia.config
  vigia.store.crypto --> vigia.config
  vigia.app -.-> vigia.store
  vigia.app -.-> vigia.services
  vigia.services --> vigia.store
  vigia.services --> vigia.signals
  vigia.services --> vigia.domain
  vigia.services --> vigia.i18n
  vigia.signals -.-> vigia.domain
  vigia.verdict -.-> vigia.domain
  vigia.policy -.-> vigia.domain
  vigia.jev -.-> vigia.domain
  vigia.channel -.-> vigia.domain
  vigia.analyst -.-> vigia.domain
  vigia.model -.-> vigia.domain
  vigia.store --> vigia.domain
  vigia.net -.-> vigia.domain
  vigia.app -.-> vigia.net
  vigia.store --> PG[(Postgres, RLS by app.business_id)]
```

## Flow of a case (target, built task by task)

```mermaid
sequenceDiagram
  participant C as vigia.channel
  participant A as vigia.app
  participant R as rules (signals, jev, verdict, policy)
  participant N as vigia.analyst
  participant S as vigia.store
  C->>A: incoming message
  A->>R: gate, Jev, deterministic signals
  A->>N: analyze (only if not obvious)
  N-->>A: Verdict JSON
  A->>R: fusion, policy
  A->>S: save case and verdict (scoped to business_id)
  A->>C: reply built from vigia.i18n templates
```

## Import contracts (`.importlinter`, checked by `make arch`)

- `domain-is-pure`: `vigia.domain` imports nothing else from `vigia`.
- `rules-see-only-domain`: `signals`, `verdict`, `policy`, `jev` never import adapters,
  services or the app.
- `adapters-are-independent`: `channel`, `analyst`, `model`, `store`, `net` do not import each other.
  `net` follows short links hop by hop and refuses non-public addresses (D-099).
- `nobody-imports-app`: only the process entrypoint loads `vigia.app`.

## Multi-tenancy

Every tenant table has `business_id` with `ENABLE` + `FORCE ROW LEVEL SECURITY` and a policy
on `current_setting('app.business_id')`. The API connects as `vigia_app` (no superuser, no
`BYPASSRLS`) and scopes each transaction with `vigia.store.db.set_business`. Migrations run
as the schema owner from a separate one-shot `migrate` service.
