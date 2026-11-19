"""Prueba rápida de las fuentes de OpenBB (lib/fuentes_openbb.py) desde GitHub Actions.

Imprime un resumen y deja el detalle en fuentes.json (artefacto del workflow). No escribe en datos.
Uso: python integracion/probar_fuentes.py [TICKER ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import fuentes_openbb as F

tks = sys.argv[1:] or ["AAPL", "SOFI", "GME"]
out = {"movers": {s: F.nasdaq_movers(s, 25) for s in F.SESIONES}}
out["cboe"] = {t: F.cboe_velas_1m(t) for t in tks}
out["finra"] = F.finra_short(tks)
scr = F.nasdaq_screener()
out["screener"] = {k: v for k, v in scr.items() if k != "filas"} | {"n": len(scr.get("filas") or []),
                                                                     "muestra": (scr.get("filas") or [])[:3]}
Path("fuentes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
for s, m in out["movers"].items():
    f = m.get("filas") or []
    print(f"movers {s}: {m.get('error') or str(len(f)) + ' filas'} · {m.get('titulo')} · "
          + ", ".join(f"{x['ticker']} {x['precio']} {x['cambio_pct']}%" for x in f[:5]))
for t, c in out["cboe"].items():
    v = c.get("velas") or []
    print(f"cboe {t}: {c.get('error') or str(len(v)) + ' velas, ' + str(v[0][0]) + ' → ' + str(v[-1])}")
print("finra:", json.dumps(out["finra"], ensure_ascii=False)[:400])
print("screener:", out["screener"].get("error") or out["screener"]["n"], json.dumps(out["screener"].get("muestra"), ensure_ascii=False)[:300])
