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

Primera corrida: 06-11 (una sola ventana). Siguiente: viernes 13-11 con la semana completa.
