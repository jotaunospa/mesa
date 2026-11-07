"""Diagnóstico: arma filas reales del Escáner (sin escribir datos) y muestra la noticia de cada una."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screener as S

ahora = S.ahora_ny()
halts, _ = S.pausas_nasdaq()
quotes = [q for q in S.universo() if "OTC" not in (q.get("fullExchangeName") or "")][:8]
out = []
for qt in quotes:
    try:
        f = S.fila(qt, halts, {}, ahora)
        ev = f["noticias_eval"]
        out.append({"ticker": f["ticker"], "headline": f["headline"], "eval": ev})
        print(f"{f['ticker']:<6} {ev['estado']:<13} fuentes={ev['fuentes']}  {(ev['validas'][0]['origen'] + ' | ' + ev['validas'][0]['titulo'][:80]) if ev['validas'] else ''}")
    except Exception as e:
        print(qt.get("symbol"), "ERROR", type(e).__name__, e)
Path("resultados").mkdir(exist_ok=True)
Path("resultados/filas.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
