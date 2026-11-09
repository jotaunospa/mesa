"""Recolector de premarket: solo junta datos, sin reglas ni Mesa (09-11).

Para qué: antes de las 09:30 Yahoo muestra el cambio del día anterior, así que el Escáner parte a
las 09:31. Esto guarda qué acciones se movían antes de la apertura, con qué volumen, float y
noticia, para medir después (tramos, gap, qué llegó a la ventana y qué no). No produce dictamen.

Fuentes (gratis, no oficiales):
- Lista: scanner público de TradingView, columnas de premarket. VIENE CON 15 MIN DE ATRASO
  ("delayed_streaming_900"): la hora de cada foto se guarda junto con ese aviso.
- Por acción: premarket consolidado de Nasdaq.com (volumen, máximo, mínimo) y noticias de
  Google Noticias + Nasdaq (lib/noticias.py, misma regla que el Escáner).

Uso: python premarket.py ventana   → fotos cada 10 min de 07:00 a 09:28 NY
     python premarket.py una_vez
Escribe en $DATOS/AAAA-MM-DD/premarket/snap-HHMM.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from lib import noticias as N

NY = ZoneInfo("America/New_York")
DATOS = Path(os.environ.get("DATOS", "datos"))
CAMBIO_MIN, VOL_MIN, N_MAX = 5, 20_000, 30
INICIO, FIN, PASO_MIN = (7, 0), (9, 28), 10
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
COLS = ["name", "description", "premarket_close", "premarket_change", "premarket_volume", "premarket_high", "premarket_low",
        "close", "float_shares_outstanding", "market_cap_basic", "exchange", "update_mode"]
_not: dict = {}


def ahora_ny() -> datetime:
    return datetime.now(NY)


def lista_tradingview() -> list[dict]:
    body = json.dumps({"markets": ["america"], "symbols": {"query": {"types": []}, "tickers": []},
                       "filter": [{"left": "premarket_change", "operation": "greater", "right": CAMBIO_MIN},
                                  {"left": "premarket_volume", "operation": "greater", "right": VOL_MIN},
                                  {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
                                  {"left": "type", "operation": "equal", "right": "stock"}],
                       "columns": COLS, "sort": {"sortBy": "premarket_change", "sortOrder": "desc"},
                       "range": [0, N_MAX]}).encode()
    req = urllib.request.Request("https://scanner.tradingview.com/america/scan", data=body,
                                 headers=dict(UA, **{"Content-Type": "application/json"}))
    js = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return [dict(zip(COLS, row["d"])) for row in js.get("data", [])]


def nasdaq_pre(tk: str) -> dict:
    try:
        req = urllib.request.Request(f"https://api.nasdaq.com/api/quote/{tk}/extended-trading?markettype=pre&assetclass=stocks&time=0",
                                     headers=dict(UA, Origin="https://www.nasdaq.com", Referer="https://www.nasdaq.com/"))
        d = (json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data") or {})
        row = ((d.get("infoTable") or {}).get("rows") or [{}])[0]
        num = lambda x: float(str(x).split()[0].replace("$", "").replace(",", "")) if x and str(x).split()[0] not in ("N/A", "--") else None
        return {"vol": num(row.get("volume")), "max": num(row.get("highPrice")), "min": num(row.get("lowPrice")),
                "ultimo": num(row.get("consolidated") or row.get("lastSalePrice")), "crudo": row}
    except Exception as e:
        return {"error": type(e).__name__}


def noticias(tk: str, nombre: str | None, hora: datetime) -> dict:
    c = _not.get(tk)
    if not c or time.time() - c[0] > 1800:
        c = (time.time(), {"google": N.google(tk, nombre), "nasdaq": N.nasdaq(tk, ahora=datetime.now(timezone.utc))})
        _not[tk] = c
    ev = N.evaluar(tk, nombre, hora, c[1])
    ev["validas"] = [{k: v.get(k) for k in ("titulo", "fuente", "fecha", "url", "origen")} for v in ev["validas"]]
    return ev


def r(x, n=4):
    try:
        return None if x is None else round(float(x), n)
    except (TypeError, ValueError):
        return None


def foto() -> dict:
    t0, ahora = time.time(), ahora_ny()
    lista = lista_tradingview()

    def fila(x):
        tk, nombre = x["name"], x.get("description")
        return {"ticker": tk, "nombre": nombre, "bolsa": x.get("exchange"),
                "pm_precio_tv": r(x.get("premarket_close")), "pm_cambio_tv": r(x.get("premarket_change"), 2),
                "pm_vol_tv": x.get("premarket_volume"), "pm_max_tv": r(x.get("premarket_high")), "pm_min_tv": r(x.get("premarket_low")),
                "cierre_ant": r(x.get("close")), "floatM_tv": r((x.get("float_shares_outstanding") or 0) / 1e6, 3) or None,
                "cap_M": r((x.get("market_cap_basic") or 0) / 1e6, 1) or None, "tv_modo": x.get("update_mode"),
                "nasdaq": nasdaq_pre(tk), "noticias_eval": noticias(tk, nombre, ahora)}

    with ThreadPoolExecutor(6) as ex:
        filas = list(ex.map(lambda x: _seguro(fila, x), lista))
    return {"hora_ny": ahora.isoformat(timespec="seconds"), "version": 1,
            "aviso": "Lista y precios de TradingView con 15 min de atraso; volumen/máximo/mínimo de Nasdaq al momento de la foto.",
            "criterio": f"premarket ≥ {CAMBIO_MIN}% y volumen ≥ {VOL_MIN:,}; NASDAQ, NYSE y AMEX; acciones comunes; hasta {N_MAX}",
            "filas": filas, "duracion_s": round(time.time() - t0, 1)}


def _seguro(fn, x):
    try:
        return fn(x)
    except Exception as e:
        return {"ticker": x.get("name"), "error": f"{type(e).__name__}: {e}"[:200]}


def guardar(s: dict) -> None:
    h = datetime.fromisoformat(s["hora_ny"])
    d = DATOS / h.strftime("%Y-%m-%d") / "premarket"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"snap-{h.strftime('%H%M')}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    # Índice y última foto, para que el Escáner los lea sin listar la carpeta.
    fotos = sorted(p.stem[5:] for p in d.glob("snap-*.json"))
    (d / "indice.json").write_text(json.dumps({"fotos": fotos}))
    (d / "ultimo.json").write_text(json.dumps(s, ensure_ascii=False, default=str))


def publicar(msg: str) -> None:
    if not (DATOS / ".git").exists():
        return
    g = ["git", "-C", str(DATOS)]
    subprocess.run(g + ["add", "-A", "--", ":(glob)*/premarket/**"], check=False)
    if subprocess.run(g + ["diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(g + ["commit", "-qm", msg], check=False)
    for _ in range(4):
        subprocess.run(g + ["pull", "-q", "--rebase", "origin", "datos"], check=False)
        if subprocess.run(g + ["push", "-q", "origin", "HEAD:datos"]).returncode == 0:
            return
        time.sleep(5)


def resumen(s: dict) -> str:
    lin = [f"{s['hora_ny']} · {len(s['filas'])} filas · {s['duracion_s']}s"]
    for f in s["filas"]:
        if "error" in f:
            lin.append(f"ERROR {f['ticker']}: {f['error']}"); continue
        ne = f["noticias_eval"]
        lin.append(f"{f['ticker']:<6} {f['pm_cambio_tv']:>7}%  {f['pm_precio_tv']}  vol {f['pm_vol_tv']}  float {f['floatM_tv']}M  "
                   f"nasdaq {f['nasdaq'].get('vol')}/{f['nasdaq'].get('max')}  noticia {ne['estado']}: "
                   f"{(ne['validas'][0]['titulo'] if ne['validas'] else '')[:55]}")
    return "\n".join(lin)


def main(modo: str) -> None:
    if modo == "una_vez":
        s = foto(); guardar(s); print(resumen(s)); publicar("premarket: foto"); return
    hoy = ahora_ny()
    ini = hoy.replace(hour=INICIO[0], minute=INICIO[1], second=0, microsecond=0)
    fin = hoy.replace(hour=FIN[0], minute=FIN[1], second=0, microsecond=0)
    if hoy.weekday() >= 5 or hoy > fin:
        print(f"fuera del premarket ({hoy:%a %H:%M} NY)"); return
    if hoy < ini:
        time.sleep((ini - hoy).total_seconds())
    n = 0
    while ahora_ny() <= fin:
        t0 = time.time()
        try:
            s = foto(); guardar(s); print(resumen(s), flush=True); n += 1
            if n % 3 == 0:
                publicar(f"premarket: fotos hasta {s['hora_ny'][11:16]}")
        except Exception:
            traceback.print_exc()
        time.sleep(max(0, PASO_MIN * 60 - (time.time() - t0)))
    publicar("premarket: fin")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "una_vez")
