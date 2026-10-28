# ciego-01: ambigüedades y silencios de specs/04 y specs/05

Evaluador a ciegas. Solo leí specs/04, specs/05 y el formato de specs/06. 20 casos (c-0167..c-0186): 9 estafa, 9 legítimo, 2 dudoso; 14 CO y 6 US.

## Ambigüedades (la spec no decide)

1. **Peso de `pide_credenciales` sin URL** (c-0171, c-0176, c-0182). 04 solo lo vuelve dura cuando hay una URL no oficial. Con el OTP pedido por chat (sin link) no queda determinista, y un aviso legítimo que dice "nunca le pediremos su clave" lo activa igual. La spec no dice si el patrón entiende negaciones ni qué peso tiene cuando no hay URL. Si pesa media, la regla 3 de 05 deja en `dudoso` un aviso de banco legítimo aunque el analista diga 0.95.
2. **Peso de `pago_personal` fuera de "liberar"** (c-0170, c-0179). Solo es dura cuando el pago es para liberar paquete, premio o deuda. No dice si "consígneme al Nequi" en otro contexto pesa media, leve o nada. Tampoco dice cómo detecta el código la intención de "liberar": no hay una lista de palabras para eso. En CO los proveedores pequeños cobran por Nequi todos los días.
3. **Regla 4 de 05: cualquier URL fuera de la lista oficial impide `legitimo`** (c-0185). Con esa regla, el link de un proveedor propio (catálogo, tienda) nunca puede salir legítimo. No dice si los dominios de la libreta cuentan como "lista oficial".
4. **La regla 3 de 05 bloquea legítimos con una sola media** (c-0186). Un recordatorio real con "último aviso" no puede salir legítimo aunque el analista diga 0.85 o más.
5. **Cómo se calcula `dominio_lookalike`** (c-0172). La spec dice "distancia ≤ 2 o homógrafo" pero no dice si compara el dominio registrable completo o solo la etiqueta sin sufijo, ni qué pasa con sufijos que no están en la PSL (`.example`, `.invalid`). Si compara el registrable completo, un homógrafo con otro TLD (bancolornbia.example) se escapa.
6. **Patrones en inglés de US.json** (c-0175). 04 dice "patrones en inglés", pero no los enumera ni explica cómo se detecta "liberar deuda" en inglés. Tampoco dice si Zelle o Venmo, que son marcas, cuentan además como `pago_personal`.
7. **Normalización de los patrones** (c-0170, c-0186). No dice si las regex ignoran tildes y mayúsculas: "cambie de numero" frente a "cambié de número", "ULTIMO AVISO" frente a "últim[oa] aviso".
8. **Choque entre categoría y esperado** (c-0185, c-0186). specs/06 exige `esperado=legitimo` para cualquier categoría `legitimo_*`, y no hay ninguna categoría para "mensaje legítimo que la política obliga a dejar en dudoso". Usé `legitimo_otro` con `dudoso` a propósito: el validador debería quejarse, y eso también es un hallazgo.

## Silencios (la spec no habla del tema)

- **Teléfonos dentro del texto** (c-0175). No hay ninguna señal para "llame al +1 555…" con un número que no es el `canal_oficial` de la marca. El vishing sin URL queda en manos del analista.
- **`autoridad` sin link** (c-0180). Solo existe la combinación con un link no oficial. Supuse que sola no pesa nada.
- **Sin analista, ningún legítimo pasa** (regla 4 de 05). En `--solo-deterministas` los 9 legítimos de este set salen `dudoso` degradado. `dominio_oficial` (leve, positiva) no cambia nada en la fusión.
- **Cómo se cuentan las medias.** No dice si dos coincidencias de la misma familia (por ejemplo "ganaste" y "bono" en `premio`) son una media o dos para la regla 2.
- **`marca_fuera_de_dominio` con la marca en el path.** Una tienda legítima con `/pagos/pse` o `/mercadolibre` en la ruta sale estafa por regla dura. No incluí caso porque necesitaba un dominio real.
- **Mensaje en otro idioma que el del paquete.** Si un negocio US recibe una estafa en español, los patrones en inglés no la ven. La spec fija el paquete al inscribir el negocio y no dice nada de esto.
- **El ejemplo de CO.json** pone `018000912345` como canal de Bancolombia. Si es un placeholder, c-0176 cita una línea que puede no estar en el paquete real. Eso no cambia el veredicto, pero sí el canal oficial que se muestra.

## Integración (Claude Code, 27 oct)
- c-0185 y c-0186 quedan como los escribió el evaluador (`legitimo_otro`, `esperado=dudoso`). La ambigüedad 8 era real: specs/06 se contradecía (`ambiguo` permite `dudoso`, `legitimo_*` exigía `legitimo`). Corregido en specs/06 y en el validador (D-074).
- c-0169 y c-0179: números de Nequi cambiados a `300 000 0000` y `300 000 0001` (no parecían ficticios; regla 10).
- Ceguera parcial: además de specs/04 y 05 el evaluador leyó specs/06 (formato del caso). No vio código, evals ni resultados. **Mismo modelo**: el agente no fija `model:` y heredó el del orquestador. No es "evaluado por otro modelo".
- Las ambigüedades 1–4 y 6–8 y los silencios quedan resueltos como precisiones en specs/04 y specs/05 (lo que el código ya hace).
- La 5 (c-0172) fue **código mal**: `dominio_lookalike` comparaba solo el registrable entero y un homógrafo con otro sufijo no daba señal; c-0172 salía estafa solo por el analista. Incidente c-0187 (+ gemelo legítimo c-0188) y arreglo en `signals/urls.py`: ahora compara también el nombre sin sufijo. Solo-deterministas: c-0172 y c-0187 pasan a estafa, fp sigue en 0.

## Resultado contra el sistema (corrida completa del 27 oct, `evals/ultimo.resultado.json`)
19/20. Estafas 9/9, legítimos 8/9 (ningún legítimo salió estafa), dudosos 2/2.
- c-0179 (legítimo → dudoso): pago al Nequi personal "como siempre" de un proveedor. `pago_personal` ya pesa leve (no entra en la fusión); la causa es la regla 4 de 05: el analista consultó `check_address_book`, la libreta está vacía en evals, recibió `unknown_supplier` y no confirmó legítimo con ≥ 0.7 → `dudoso` 0.55. Clasificación: **spec incompleta** en 05 (qué hace la regla 4 sin libreta), corregida como precisión. El caso queda en dudoso, que es lo prudente. En T-10 entra un gemelo `origen=incidente` con el proveedor en la libreta y `esperado=legitimo`.
- Una diferencia de código (c-0172, arriba); con el analista no cambió ningún veredicto de estafa.
