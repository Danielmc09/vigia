# evals/

- `casos.jsonl` — el set. Formato y metas en `specs/06`. Cada línea lleva `hipotesis` escrita antes de correr.
- `adversarios.jsonl` — secuencias de ataque al sistema (inyección, crescendo, inundación, no inscrito). Se evalúa el comportamiento del sistema, no el veredicto.
- `ciego-01.jsonl` + `ciego-01.md` — casos escritos por otro modelo leyendo solo `specs/04` y `specs/05`.
- `muestras/` — adjuntos (png, ogg, pdf, eml) referenciados por `fuente`. Logos ficticios en los sintéticos; lo real, anonimizado.
- `*.resultado.json` — evidencia de cada corrida; nunca se edita a mano. `ultimo.resultado.json` es la referencia para detectar regresión.

Comandos: `make evals-rapido` (30 casos `rapido:true`, a diario), `make evals` (completo, máx. 2 veces al día), `python medir.py evals/casos.jsonl --solo-deterministas` (cuánto resuelve el código sin modelo).

Regla: un caso que falló en producción o que Angel corrigió a mano entra aquí con `origen="incidente"` **antes** de tocar código o prompt.
