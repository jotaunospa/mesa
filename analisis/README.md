# Revisión de reglas (Mesa de Papel + Escáner)

Tres piezas, en orden. Todo es medición en papel: no predice nada.

1. **Calce de datos** (`calce.py`): compara campo a campo cada lectura del Escáner guardada en la
   Mesa con la foto de origen. Necesita un clon de la rama `datos` y los documentos de la Mesa
   (ArtifactData `query` de `data/users/me` con `src == escaner`, guardados con `out_dir`).
2. **Veredictos con el código real de la Mesa** (`filas.py` + `veredictos.js`):
   - `python analisis/filas.py DATOS filas.json` traduce todas las fotos al formato de la Mesa.
   - Copia la Mesa (Artifact `read`) a `mesa-test.html` y agrega antes de `/* arranque */`:
     `window.__mesa={evaluate:evaluate,bestOf:bestOf,analyzeChart:analyzeChart,phaseAt:phaseAt,sanitizeCandles:sanitizeCandles,casiOf:casiOf,SHORT:SHORT,DEF:DEF};`
   - Sirve `mesa-test.html` y `filas.json` en la misma carpeta (`python3 -m http.server 8766`) y
     corre `node analisis/veredictos.js` (usa el Playwright de la sesión).
3. **Prueba mecánica** (`backtest.py veredictos.json DATOS`): simula cada setup válido con las
   velas de 1 minuto guardadas (entrada en el precio del setup o en la apertura si ya lo pasó,
   stop, objetivo 2R, cierre 10:30, costos supuestos de la Mesa), con las reglas completas,
   quitando una regla a la vez y con umbrales de stop/VWAP más flojos.

**Todo de una vez:** `analisis/semana.sh DIR_DATOS mesa-test.html SALIDA AAAA-MM-DD ...` corre los pasos 2 y 3 y
`criterios.py`, que aplica los criterios de abajo y escribe `SALIDA/informe.md`. La tarea programada
"Mesa: revisión semanal de reglas" lo corre cada viernes y sube el informe a `analisis/informes/`.

Primera corrida: 06-11 (una sola ventana). Siguiente: viernes 13-11 con la semana completa.

## Criterios para la revisión del 13-11 (fijados el 06-11, antes de ver la semana)

Pregunta: ¿alguna regla bloquea operaciones que habrían salido bien? Se decide con estas reglas,
escritas antes de mirar los datos para no acomodar los criterios al resultado.

- Unidad: setup detectado por el código real de la Mesa, simulado con `backtest.py` (costos supuestos,
  una operación por acción y día, cierre 10:30).
- Para cada regla se mide lo que bloquea: las operaciones que aparecen al quitar solo esa regla.
- **Sobreprotectora** (se propone ajustarla) solo si se cumplen las tres:
  1. bloquea 8 operaciones o más en la semana;
  2. esas operaciones dan en promedio +0,3 R neto o más;
  3. sigue positivo al quitar la mejor operación y la mejor acción (no depende de un solo caso).
- **Protege** si lo bloqueado da R neto negativo. Se mantiene.
- **Sin conclusión** si bloquea menos de 8. Se mantiene y se mide otra semana.
- Las grandes subidas que el sistema no marcó se revisan aparte: solo cuentan como pérdida del
  sistema si en sus velas había un setup que se podía tomar en ese momento (no mirando el final del día).
- Ninguna regla se cambia en mitad de la semana: la semana debe medirse con las mismas reglas.
- Un ajuste propuesto el 13-11 es provisional; se confirma con una segunda semana antes de darlo por bueno.

## Línea base y registro de aprendizaje (07-11)

- **Línea base** (`backtest.py`, sección final): entrar a ciegas en la vela siguiente a la primera foto de
  cada acción de la lista en la ventana, stop 8%, objetivo 2R, mismos costos. Si el sistema no supera a
  esta entrada al azar, no hay ventaja aunque su número salga positivo. `criterios.py` las compara.
- **Registro** (`registro.py`, lo corre `semana.sh`): `analisis/registro/AAAA-MM-DD.jsonl` guarda cada
  GO, LISTO, VIGILAR y "casi" (una sola regla dura fallando cerca del límite) con su perfil, la regla que
  falló, el setup, la versión de reglas y lo que pasó 15 y 30 min después (máximo, mínimo, último, en %
  contra el precio de la foto). Se acumula semana a semana para buscar patrones con muchos casos.
  El 05-11 se registró con noticias solo de Yahoo (antes del feed de tres fuentes).

## Tramos del día y excursiones (07-11, solo medición)

- `tramos.py`: para cada candidata (todas las de `resultados.json`), retorno por tramo: noche (cierre
  anterior → primer precio del premarket), premarket (→ 09:30), apertura (→ 09:35), ventana (09:35 → 10:15)
  y resto (→ cierre). Separado por noticia, gap, float, temperatura y dictamen. Bruto y neto de costos.
  Los tramos se encadenan al retorno de cierre a cierre. El cierre anterior se calcula de las fotos
  (precio / (1 + cambio)); el cierre del día es el oficial cuando la acción sale en las fotos del día
  siguiente, y si no la última vela de 1 min (difiere ~1% del oficial). Lo que falta queda "sin dato".
- `excursiones.py`: para cada setup detectado (misma simulación que `backtest.py`, cumpla o no los
  umbrales): MAE y MFE hasta la salida, R bruto y neto, deslizamiento observado al entrar y al salir con
  stop, y velocidad de subidas contra bajadas en velas de 1 min.
- `costos.py`: spread de Nasdaq de la fila + deslizamiento escalonado de la Mesa + comisión de Racional
  **supuesta** (0,25% por lado, no confirmada).
- Las dos entran a `semana.sh` y al informe. n < 30: "muestra chica, no concluye".
- Prueba del 07-11 (días 05 y 06, 97 candidatas): en RAYA, STAK, SNT y ARDT los tramos encadenados
  reproducen el retorno de cierre a cierre de `resultados.json` con error < 0,01 pp.
