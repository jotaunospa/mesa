"""Prueba rápida de las fuentes de OpenBB (lib/fuentes_openbb.py) desde GitHub Actions.

Imprime un resumen y deja el detalle en fuentes.json (artefacto del workflow). No escribe en datos.
Uso: python integracion/probar_fuentes.py [TICKER ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import fuentes_openbb as F, stocktwits as ST

tks = sys.argv[1:] or ["AAPL", "SOFI", "GME"]
out = {"movers": {s: F.nasdaq_movers(s, 25) for s in F.SESIONES}}
out["cboe"] = {t: F.cboe_velas_1m(t) for t in tks}
out["finra"] = F.finra_short(tks)
scr = F.nasdaq_screener()
out["screener"] = {k: v for k, v in scr.items() if k != "filas"} | {"n": len(scr.get("filas") or []),
                                                                     "muestra": (scr.get("filas") or [])[:3]}
out["stocktwits"] = {t: ST.medir(t) for t in tks}
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
print("stocktwits:", json.dumps(out["stocktwits"], ensure_ascii=False)[:400])

# ¿jsDelivr sirve la rama datos? (tope de 50 MB por rama en su CDN de GitHub)
import urllib.request
for url in ["https://cdn.jsdelivr.net/gh/jotaunospa/mesa@datos/index.json",
            "https://data.jsdelivr.com/v1/packages/gh/jotaunospa/mesa@datos"]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=F.UA), timeout=30) as r:
            cuerpo = r.read()
            print(f"jsdelivr {url}: {r.status}, {len(cuerpo)} bytes · {cuerpo[:160]!r}")
    except Exception as e:
        cuerpo = getattr(e, "read", lambda: b"")()[:200]
        print(f"jsdelivr {url}: {type(e).__name__} {e} · {cuerpo!r}")

# Cboe contra Yahoo: ¿la hora de cada vela marca el inicio o el fin del minuto? (decide si sirve de respaldo)
try:
    import yfinance as yf
    from zoneinfo import ZoneInfo
    for t in tks[:2]:
        y = yf.Ticker(t).history(period="1d", interval="1m", prepost=False)
        y.index = y.index.tz_convert(ZoneInfo("America/New_York"))
        yv = {i.strftime("%H:%M"): (round(x.Open, 4), round(x.Close, 4), int(x.Volume)) for i, x in y.iterrows()}
        cv = {v[0][11:16]: (v[1], v[4], v[5]) for v in (out["cboe"].get(t) or {}).get("velas") or []}
        for hh in ["09:30", "09:31", "09:32", "12:00", "15:59"]:
            print(f"cboe-vs-yahoo {t} {hh}: yahoo {yv.get(hh)} · cboe {cv.get(hh)}")
except Exception as e:
    print("cboe-vs-yahoo: error", e)
