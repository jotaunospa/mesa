#!/usr/bin/env bash
# Revisión semanal de reglas. Uso: [RULES_V=v1.4] analisis/semana.sh DIR_DATOS MESA_TEST_HTML SALIDA_DIR DIA1 [DIA2 ...]
# DIR_DATOS: clon de la rama `datos`. MESA_TEST_HTML: la Mesa con window.__mesa expuesto (README.md).
set -euo pipefail
DATOS=$1; MESA=$2; OUT=$3; shift 3
AQUI=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT/srv"
python3 "$AQUI/filas.py" "$DATOS" "$OUT/srv/filas.json" "$@"
cp "$MESA" "$OUT/srv/mesa-test.html"
(cd "$OUT/srv" && python3 -m http.server 8766 >/dev/null 2>&1) & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for i in $(seq 1 20); do curl -s -o /dev/null localhost:8766/mesa-test.html && break; sleep 0.5; done
BASE=http://localhost:8766 OUT="$OUT/veredictos.json" node "$AQUI/veredictos.js"
BT_JSON="$OUT/backtest.json" python3 "$AQUI/backtest.py" "$OUT/veredictos.json" "$DATOS" > "$OUT/backtest.txt"
python3 "$AQUI/criterios.py" "$OUT/backtest.json" "$OUT/veredictos.json" > "$OUT/informe.md"
python3 "$AQUI/tramos.py" "$DATOS" "$OUT/tramos.json" "$OUT/tramos.md" "$@"
python3 "$AQUI/excursiones.py" "$OUT/veredictos.json" "$DATOS" "$OUT/excursiones.json" "$OUT/excursiones.md"
{ echo; cat "$OUT/tramos.md"; echo; cat "$OUT/excursiones.md"; } >> "$OUT/informe.md"
python3 "$AQUI/registro.py" "$OUT/veredictos.json" "$DATOS" "${RULES_V:-?}" "$AQUI/registro"
echo "listo: $OUT/informe.md (registro en analisis/registro/)"
