# VIGÍA

*Centinela anti-estafa para pymes, construido durante el Meta Global AI Developer Hackathon (27 oct – 4 nov 2026).*

VIGÍA es el número de WhatsApp y la dirección de correo a los que una pyme
reenvía cualquier cosa sospechosa — un texto, una captura, una nota de voz, un
PDF, un link, un correo — y recibe en segundos un veredicto en lenguaje claro:
**estafa / legítimo / dudoso**, por qué, y qué hacer. Si es estafa, el resto
del equipo también queda avisado.

**GIF: pendiente, T-22 lo grabará.**

## Métricas (última corrida con fecha)

Todavía no hay una cifra final limpia; esta es la última corrida con fecha,
con su advertencia, sin inventar nada:

| Fecha / commit | Casos | Recall de estafa | Falsas alarmas en legítimos | Dudosos | Costo | Nota |
|---|---|---|---|---|---|---|
| 31-10-2026, `859c500` | 205 | 98,2% | 2,3% | 8,3% | $0,750 | El gate automático marcó una regresión en exactitud y en la tasa de dudosos en esta corrida — no es una cifra final limpia, ver `ESTADO.md` |

Meta (`OBJETIVOS.md`, ≥80 casos): `recall_estafa ≥ 0.90`, `fp_legitimo ≤
0.10`, `dudosos ≤ 0.25`.

## Correr la demo

Necesita Docker (con Compose) y [uv](https://docs.astral.sh/uv/).

```
git clone <este repo> && cd vigia
cp .env.example .env   # MODEL_API_KEY es opcional; sin ella la demo corre solo con reglas
make demo
```

Levanta Postgres y la API con Docker Compose, inscribe un negocio de ejemplo
con 3 miembros, envía 5 casos de ejemplo por el canal local, y abre el panel
con los veredictos en `http://localhost:8000/panel?token=<VIGIA_PANEL_TOKEN>`
(imprime el link completo). No hay instancia hospedada: `make demo` en tu
máquina es la forma de probarlo. El link pone una cookie HttpOnly y redirige a
`/panel`, así el token sale de la barra de direcciones. Escapa el token si
tiene `+`, `/` o `=`. Sin `MODEL_API_KEY` lo avisa y corre solo con reglas (sin
analista). Las llaves de `.env.example` son llaves públicas de demo:
`VIGIA_MODO=prod` las rechaza.

Ver `DECLARACION.md` para lo que se construyó durante el evento.

## Llamadas a modelos de Meta vs. código

En runtime (`VIGIA_MODO=eval|prod`) cada llamada a modelo es un modelo de
Meta: Muse Spark 1.3 (bucle del analista, lectura de adjuntos, y el juez de
respaldo de la compuerta), SAM 3.1 (recortes), Muse Voice Transcribe (notas de
voz). Todo lo demás detrás de un nombre de "modelo" es código determinista:
las señales del paquete de país, el piso de manipulación (D-051), la fusión y
la política.

TypeSafe Jev es una herramienta **solo de desarrollo** (juez de pasos), nunca
se llama en runtime: `Settings` rechaza `JEV_BACKEND=real` salvo que
`VIGIA_MODO=dev` esté declarado explícitamente (el modo por defecto no cuenta;
la imagen de Docker pone `VIGIA_MODO=prod`; T-08b, D-085). La compuerta
(`jev/gate.py`) siempre corre primero el piso determinista; su respaldo de
modelo es Spark en `eval`/`prod` y, solo en `dev`, opcionalmente Jev.

## Qué hace cada modelo de Meta

| Modelo | Hace |
|---|---|
| Muse Spark 1.3 | El bucle del analista con herramientas (búsqueda web, consulta de links ya resueltos), lee capturas/PDF cuando las reglas solas no alcanzan, y es el juez de respaldo de la compuerta para el puntaje de manipulación en runtime |
| SAM 3.1 | Recorta logos y códigos QR de una captura; el QR se decodifica después en código, no por el modelo |
| Muse Voice Transcribe | Convierte una nota de voz de WhatsApp en texto, en código, antes de que cualquier señal o modelo la vea |

## Rúbrica (`OBJETIVOS.md`, Objetivo 2 — maximizar la nota del jurado)

| Criterio | Peso | Lo que lo gana |
|---|---|---|
| Impacto | 30% | Problema con cifras + pyme real en el video + onboarding sin app |
| Meta tooling | 25% | Spark como agente orquestando SAM y Voice; QR recortado; audio; Glimmer si se logra |
| Demo | 25% | Nota de voz abre el video; alerta al equipo; correo BEC; legítimo en 2 s |
| Técnica | 20% | medir.py + gate, preregistro, adversarios, RLS, 3 capas |

Bonus objetivo: Agentic (5k) y Open Weights (10k) por jueces; Community Vote
(10k) por LinkedIn y grupos. Most Viral (X) no es objetivo.

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
[`specs/16`](specs/16-cifrado-de-datos.md); cómo reportar una vulnerabilidad en
[`SECURITY.md`](SECURITY.md).

## Fuera de alcance

Lo que NO es objetivo de esta hackathon: monetizar, multi-país completo, MCP
para terceros, auditar chatbots ajenos, cubrir zero-click. Todo eso vive en
"Ideas para después."

## Benchmark

Pendiente, ver `specs/14` §2 (VIGÍA-Bench, publicado por separado una vez
construido).

## Más

- [`docs/MAP.md`](docs/MAP.md) — una línea por módulo, su capa y
  responsabilidad.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — diagramas de componentes y
  flujo, contratos de importación, multi-tenancy.
- [`docs/api/`](docs/api/) — referencia de API generada (`pdoc`).
- [`docs/decisions/`](docs/decisions/) — cada decisión técnica (ADR-001 a
  ADR-112), migrada al inglés desde `DECISIONES.md`.
