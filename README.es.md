# VIGÍA

*Centinela anti-estafa para pymes, construido durante el Meta Global AI Developer Hackathon (27 oct – 4 nov 2026).*

VIGÍA es el número de WhatsApp y la dirección de correo a los que una pyme
reenvía cualquier cosa sospechosa — un texto, una captura, una nota de voz, un
PDF, un link, un correo — y recibe en segundos un veredicto en lenguaje claro:
**estafa / legítimo / dudoso**, por qué, y qué hacer. Si es estafa, el resto
del equipo también queda avisado.

**GIF: pendiente, T-22 lo grabará.**

## Métricas (última corrida completa)

Medidas por `tools/measure.py` sobre `evals/casos.jsonl`; el resultado crudo
es `evals/2026-10-31.resultado.json`. `make docs-check` falla si esta fila se
aleja de la corrida con fecha más reciente en `evals/`.

| Fecha | Casos | Exactitud | Recall de estafa | Falsas alarmas en legítimos | Dudosos | Costo | Latencia mediana | Commit | Gate |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-31 | 231 | 93,5% | 98,5% | 2,1% | 6,5% | $0,879 | 3,22 s | `a474a96` | aprobado, 0 regresiones |

En 73 de los 231 casos las reglas deterministas ya eran concluyentes, así que VIGÍA
se saltó el analista a propósito ([`ADR-061`](docs/decisions/ADR-061.md)): solo el
chequeo de manipulación de la compuerta llamó a un modelo, con una mediana de $0,0015 y
1,5 s por caso, contra $0,0047 y 4,0 s con el analista. En otros 15, Muse Spark leyó el
PDF o la captura y las reglas decidieron sobre el texto extraído. El analista corrió en
los 143 restantes. El archivo de resultado cuenta esos 88 como `degradados` (sin
analista); ninguno de los 231 se quedó sin analista por un modelo no disponible o un
rechazo.

Meta (≥80 casos): `recall_estafa ≥ 0.90`, `fp_legitimo ≤ 0.10`,
`dudosos ≤ 0.25`.

## Correr la demo

Necesita Docker (con Compose) y [uv](https://docs.astral.sh/uv/).

```
git clone <este repo> && cd vigia
cp .env.example .env   # MODEL_API_KEY es opcional; sin ella la demo corre solo con reglas
make demo
```

Levanta Postgres y la API con Docker Compose, inscribe un negocio de ejemplo
con 3 miembros, envía 5 casos de ejemplo por el canal local, y abre el panel
con los veredictos en `http://localhost:18000/panel?token=<VIGIA_PANEL_TOKEN>`
(imprime el link completo). No hay instancia hospedada: `make demo` en tu
máquina es la forma de probarlo. El link pone una cookie HttpOnly y redirige a
`/panel`, así el token sale de la barra de direcciones. Escapa el token si
tiene `+`, `/` o `=`. Sin `MODEL_API_KEY` lo avisa y corre solo con reglas (sin
analista). Las llaves de `.env.example` son llaves públicas de demo:
`VIGIA_MODO=prod` las rechaza.

El primer `make demo` descarga y construye las imágenes de Docker y puede tardar
varios minutos; después tarda segundos. Sin `MODEL_API_KEY` VIGÍA nunca responde
"legítimo" (esa decisión la toma el analista), así que el ejemplo legítimo sale
"dudoso"; el panel lo explica. Postgres se publica en `127.0.0.1:55432` y la API en
`127.0.0.1:18000`; si alguno está ocupado, define `VIGIA_DB_PORT` o `VIGIA_API_PORT`
en `.env` (y el puerto en `DATABASE_URL`), corre `docker compose down` y otra vez
`make demo`.

Ver `DECLARACION.md` para lo que se construyó durante el evento.

## Llamadas a modelos de Meta vs. código

En runtime (`VIGIA_MODO=eval|prod`) cada llamada a modelo es un modelo de
Meta: Muse Spark 1.3 (bucle del analista, lectura de adjuntos, y el juez de
respaldo de la compuerta), SAM 3.1 (recortes), Muse Voice Transcribe (notas de
voz). Todo lo demás detrás de un nombre de "modelo" es código determinista:
las señales del paquete de país, el piso de manipulación (D-051), la fusión y
la política.

Un juez externo que no es de Meta se usó solo durante el desarrollo (juez de
pasos) y se sacó del código, así que nunca puede llamarse en runtime (T-08b,
D-085). La compuerta (`guard/gate.py`) siempre corre primero el piso
determinista; su respaldo de modelo es Spark en `eval`/`prod` y el mock
determinista en `dev`.

## Qué hace cada modelo de Meta

| Modelo | Hace |
|---|---|
| Muse Spark 1.3 | El bucle del analista con herramientas (búsqueda web, consulta de links ya resueltos), lee capturas/PDF cuando las reglas solas no alcanzan, y es el juez de respaldo de la compuerta para el puntaje de manipulación en runtime |
| SAM 3.1 | Recorta logos y códigos QR de una captura; el QR se decodifica después en código, no por el modelo |
| Muse Voice Transcribe | Convierte una nota de voz de WhatsApp en texto, en código, antes de que cualquier señal o modelo la vea |

## Para el jurado

Dónde se puede comprobar cada criterio de evaluación en este repo o en la demo:

| Criterio | Peso | Dónde verlo |
|---|---|---|
| Impacto | 30% | Una pyme sin equipo de seguridad recibe un veredicto por WhatsApp sin instalar ninguna app; si es estafa, todo el equipo queda avisado (`src/vigia/services/whatsapp.py`, `make demo`) |
| Meta tooling | 25% | Muse Spark como analista con herramientas (`src/vigia/analyst/loop.py`), recortes de SAM 3.1 con el QR decodificado en código, Muse Voice Transcribe para notas de voz (ver "Qué hace cada modelo de Meta" arriba) |
| Demo | 25% | `make demo`: casos de ejemplo por el canal local y el panel de operador con sus veredictos |
| Técnica | 20% | Exactitud medida con gate de regresión (`tools/measure.py`, la fila de métricas de arriba), evals adversarios y a ciegas (`evals/adversarios.jsonl`, `evals/ciego-01.md`), seguridad por fila y cifrado (`docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`), cada decisión como ADR (`docs/decisions/`) |

## Seguridad

Categorías OWASP que este repo prueba, mapeadas al test que las cubre:

| Categoría OWASP | Cubierta por |
|---|---|
| A01 — Control de acceso roto | `tests/test_owasp.py` (`test_a01_business_sees_only_its_own_rows`, `test_a01_without_business_context_no_rows_are_visible`, `test_a01_cannot_write_rows_for_another_business`) |
| A03 — Inyección | `tests/test_owasp.py` (`test_a03_detector_flags_runtime_built_queries`, `test_a03_no_runtime_built_sql_in_src`, `test_a03_business_id_is_bound_not_interpolated`) |
| A05 — Configuración de seguridad incorrecta | `tests/test_owasp.py` (`test_a05_security_headers_are_set`, `test_a05_no_api_docs_in_prod`, `test_a05_public_texts_never_name_internal_signals`) |
| A09 — Fallas de registro y monitoreo | `tests/test_owasp.py` (`test_a09_case_logs_carry_ids_but_no_pii`, `test_a09_uvicorn_errors_are_json_without_pii`) |
| A10 — Server-side request forgery | `tests/test_net.py`, sobre `src/vigia/net/unroll.py` (D-099): el desenrollado de links cortos rechaza direcciones no públicas, limita saltos y tiempo, y nunca reenvía cookies |

Documento completo: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) y
[`docs/PRIVACY.md`](docs/PRIVACY.md); diseño de cifrado en
[`ADR-020`](docs/decisions/ADR-020.md) y [`ADR-025`](docs/decisions/ADR-025.md);
cómo reportar una vulnerabilidad en
[`SECURITY.md`](SECURITY.md).

## Fuera de alcance

No son objetivos de esta versión de la hackathon: monetización, cobertura
multi-país completa, un servidor MCP para terceros, auditar chatbots de
terceros y ataques zero-click.

## Benchmark

Pendiente: VIGÍA-Bench se publicará por separado una vez construido.

## Más

- [`docs/MAP.md`](docs/MAP.md) — una línea por módulo, su capa y
  responsabilidad.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — diagramas de componentes y
  flujo, contratos de importación, multi-tenancy.
- Referencia de API: se genera localmente con `make docs` (`pdoc`, en `docs/api/`).
- [`docs/decisions/`](docs/decisions/) — cada decisión técnica (ADR-001 a
  ADR-112), migrada al inglés desde el registro de decisiones de trabajo.
