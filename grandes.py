"""Recolector de acciones grandes: solo junta datos, sin reglas ni Mesa (07-11).

Para qué: tener historia de acciones grandes "en juego" (las que más se mueven ese día con
capitalización de 10 mil millones de USD o más) por si más adelante se abre una variante de
la Mesa con ellas. No produce dictamen ni entra a la Mesa: así no ensucia la medición de la
estrategia de acciones chicas.

Fuentes (gratis, no oficiales): Yahoo vía yfinance; noticias de Google Noticias y Nasdaq
(lib/noticias.py, misma regla de relevancia que el Escáner).

Uso:
  python grandes.py una_vez   → una foto ahora
  python grandes.py ventana   → fotos cada 3 min entre 09:31 y 10:20 NY (como el Escáner)
  python grandes.py cierre    → velas de 1 min del día completo de todo lo que salió en las fotos
Escribe en $DATOS/AAAA-MM-DD/grandes/: snap-HHMM.json, velas/TICKER.json ([["HH:MM",o,h,l,c,v]]).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery as Q

from lib import noticias as N

NY = ZoneInfo("America/New_York")
DATOS = Path(os.environ.get("DATOS", "datos"))
CAP_MIN = 10e9            # capitalización mínima (USD)
VOL_MIN = 500_000         # volumen del día mínimo
N_SUBEN, N_BAJAN = 25, 15  # las que más suben y las que más bajan
INDICES = ["SPY", "QQQ"]
INICIO, FIN, PASO_MIN = (9, 31), (10, 20), 3

_diario: dict = {}
_perfil: dict = {}
_info: dict = {}
_not: dict = {}
_crudas: dict = {}  # titulares sin filtrar, para re-evaluar con reglas futuras


def r(x, n=2):
    try:
        return None if x is None or x != x else round(float(x), n)
    except (TypeError, ValueError):
        return None


def ahora_ny() -> datetime:
    return datetime.now(NY)


def universo() -> list[dict]:
    base = [Q("eq", ["region", "us"]), Q("gte", ["intradaymarketcap", CAP_MIN]), Q("gte", ["dayvolume", VOL_MIN])]
    out, vistos = [], set()
    for asc, n in ((False, N_SUBEN), (True, N_BAJAN)):
        res = yf.screen(Q("and", base), sortField="percentchange", sortAsc=asc, size=n)
        for q in res.get("quotes", []):
            if q["symbol"] not in vistos:
                vistos.add(q["symbol"]); out.append(q)
    return out


def diario(tk: str) -> dict:
    """Una vez por día: cierre previo, ATR de 14 días (%) y volumen promedio de 20 días."""
    if tk not in _diario:
        o = {}
        try:
            h = yf.Ticker(tk).history(period="2mo", interval="1d")
            h = h[h.index.tz_convert(NY).date < ahora_ny().date()]
            if len(h) >= 15:
                pc = h["Close"].shift(1)
                tr = pd.concat([h["High"] - h["Low"], (h["High"] - pc).abs(), (h["Low"] - pc).abs()], axis=1).max(axis=1)
                o = {"cierre_prev": float(h["Close"].iloc[-1]), "atr14_pct": r(tr.tail(14).mean() / h["Close"].iloc[-1] * 100),
                     "vol_prom20_M": r(h["Volume"].tail(20).mean() / 1e6, 3)}
        except Exception as e:
            o = {"error": f"{type(e).__name__}: {e}"[:120]}
        _diario[tk] = o
    return _diario[tk]


def perfil(tk: str) -> dict:
    """Volumen acumulado promedio a cada minuto del día (10 sesiones, velas de 5 min), para el RVOL por hora."""
    if tk not in _perfil:
        p = {}
        try:
            h = yf.Ticker(tk).history(period="15d", interval="5m", prepost=False)
            idx = h.index.tz_convert(NY)
            h = h.assign(_d=idx.date, _m=idx.hour * 60 + idx.minute)
            h = h[(h["_d"] < ahora_ny().date()) & (h["_m"] >= 570) & (h["_m"] < 960)]
            dias = sorted(h["_d"].unique())[-10:]
            sub = h[h["_d"].isin(dias)]
            for m in range(575, 961, 5):
                if dias:
                    p[m] = float(sub[sub["_m"] < m].groupby("_d")["Volume"].sum().reindex(dias, fill_value=0).mean())
        except Exception:
            pass
        _perfil[tk] = p
    return _perfil[tk]


def info(tk: str) -> dict:
    if tk not in _info:
        try:
            i = yf.Ticker(tk).info or {}
            _info[tk] = {k: i.get(k) for k in ("longName", "shortName", "sector", "industry", "country")}
        except Exception:
            _info[tk] = {}
    return _info[tk]


def noticias(tk: str, nombre: str | None, hora: datetime) -> dict:
    """Google Noticias + Nasdaq con caché de 15 min; se evalúa con la regla de lib/noticias.py."""
    c = _not.get(tk)
    if not c or time.time() - c[0] > 900:
        c = (time.time(), {"google": N.google(tk, nombre), "nasdaq": N.nasdaq(tk, ahora=datetime.now(timezone.utc))})
        _not[tk] = c
    _crudas[tk] = c[1]
    ev = N.evaluar(tk, nombre, hora, c[1])
    ev["validas"] = [{k: v.get(k) for k in ("titulo", "fuente", "fecha", "url", "origen")} for v in ev["validas"]]
    return ev


def velas(tks: list[str]) -> dict:
    """Velas de 1 min de hoy (con premarket) de todas en una sola descarga."""
    out = {}
    if not tks:
        return out
    df = yf.download(tks, period="1d", interval="1m", prepost=True, group_by="ticker", threads=True, progress=False, auto_adjust=False)
    for tk in tks:
        try:
            d = (df[tk] if isinstance(df.columns, pd.MultiIndex) else df).dropna(subset=["Close"])
            out[tk] = [[i.tz_convert(NY).strftime("%H:%M"), r(x.Open, 4), r(x.High, 4), r(x.Low, 4), r(x.Close, 4), int(x.Volume or 0)]
                       for i, x in d.iterrows()]
        except Exception:
            pass
    return out


def vwap(v: list) -> float | None:
    pv = vv = 0.0
    for t, o, h, l, c, vol in v:
        if "09:30" <= t < "16:00" and None not in (h, l, c):
            pv += (h + l + c) / 3 * vol; vv += vol
    return pv / vv if vv else None


def foto() -> tuple[dict, dict]:
    t0 = time.time()
    ahora = ahora_ny()
    quotes = universo()
    tks = [q["symbol"] for q in quotes]
    vel = velas(tks + INDICES)
    m = ahora.hour * 60 + ahora.minute

    def fila(q):
        tk = q["symbol"]
        inf, d = info(tk), diario(tk)
        nombre = inf.get("longName") or inf.get("shortName") or q.get("longName") or q.get("shortName")
        v = vel.get(tk) or []
        reg = [x for x in v if "09:30" <= x[0] < "16:00"]
        pre = [x for x in v if x[0] < "09:30"]
        vw = vwap(v)
        p = q.get("regularMarketPrice")
        vol = q.get("regularMarketVolume") or 0
        pf = perfil(tk)
        base_h = pf.get(m - m % 5 + 5) if pf else None
        abre, prev = q.get("regularMarketOpen"), q.get("regularMarketPreviousClose") or d.get("cierre_prev")
        return {
            "ticker": tk, "nombre": nombre, "sector": inf.get("sector"), "industria": inf.get("industry"), "pais": inf.get("country"),
            "bolsa": q.get("fullExchangeName"), "cap_B": r((q.get("marketCap") or 0) / 1e9, 1),
            "price": r(p, 4), "chg": r(q.get("regularMarketChangePercent")), "gap_pct": r((abre / prev - 1) * 100) if abre and prev else None,
            "volM": r(vol / 1e6, 3), "vol_prom20_M": d.get("vol_prom20_M"),
            "rvol": r(vol / (d["vol_prom20_M"] * 1e6), 2) if d.get("vol_prom20_M") else None,
            "rvol_hora": r(vol / base_h, 2) if base_h else None,
            "atr14_pct": d.get("atr14_pct"),
            "dayHigh": r(q.get("regularMarketDayHigh"), 4), "dayLow": r(q.get("regularMarketDayLow"), 4),
            "pm_high": r(max(x[2] for x in pre), 4) if pre else None, "pm_vol_M": r(sum(x[5] for x in pre) / 1e6, 3) if pre else None,
            "vwap": r(vw, 4), "sobre_vwap_pct": r((p / vw - 1) * 100) if p and vw else None,
            "or15": [r(max(x[2] for x in reg[:15]), 4), r(min(x[3] for x in reg[:15]), 4)] if len(reg) >= 15 else None,
            "resultados_ts": q.get("earningsTimestamp"),
            "edad_s": int(time.time() - q["regularMarketTime"]) if q.get("regularMarketTime") else None,
            "noticias_eval": noticias(tk, nombre, ahora),
        }

    filas, errores = [], []
    with ThreadPoolExecutor(8) as ex:
        for q, f in zip(quotes, ex.map(lambda q: _seguro(fila, q), quotes)):
            (errores if "error" in f else filas).append(f if "error" not in f else {"ticker": q["symbol"], **f})
    idx = {}
    for tk in INDICES:
        v, d = vel.get(tk) or [], diario(tk)
        if v and d.get("cierre_prev"):
            idx[tk] = r((v[-1][4] / d["cierre_prev"] - 1) * 100)
    snap = {"hora_ny": ahora.isoformat(timespec="seconds"), "version": 1,
            "criterio": f"capitalización ≥ {CAP_MIN / 1e9:.0f} mil millones USD, volumen ≥ {VOL_MIN:,}; las {N_SUBEN} que más suben y las {N_BAJAN} que más bajan",
            "indices_chg": idx, "filas": filas, "errores": errores, "duracion_s": round(time.time() - t0, 1)}
    return snap, {tk: vel[tk] for tk in tks if tk in vel}


def _seguro(fn, q):
    try:
        return fn(q)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def guardar(snap: dict, vel: dict) -> Path:
    h = datetime.fromisoformat(snap["hora_ny"])
    d = DATOS / h.strftime("%Y-%m-%d") / "grandes"
    (d / "velas").mkdir(parents=True, exist_ok=True)
    p = d / f"snap-{h.strftime('%H%M')}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str))
    for tk, v in vel.items():
        (d / "velas" / f"{tk}.json").write_text(json.dumps(v, separators=(",", ":")))
    # Titulares crudos del día (Google + Nasdaq), uno por ticker sin repetir, para re-evaluarlos después.
    pn = d / "noticias.json"
    todo = json.loads(pn.read_text()) if pn.exists() else {}
    for tk, fuentes in _crudas.items():
        vistos = {(x.get("titulo"), x.get("origen")) for x in todo.get(tk, [])}
        for items in fuentes.values():
            for x in items or []:
                k = (x.get("titulo"), x.get("origen"))
                if k not in vistos:
                    vistos.add(k)
                    todo.setdefault(tk, []).append({c: x.get(c) for c in ("titulo", "fuente", "fecha", "solo_dia", "url", "origen")})
    pn.write_text(json.dumps(todo, ensure_ascii=False, separators=(",", ":")))
    return p


def cierre() -> None:
    """Velas del día completo de todo lo que apareció en las fotos de hoy."""
    h = ahora_ny()
    if h.hour * 60 + h.minute < 16 * 60 + 5 and not os.environ.get("FORZAR"):
        print(f"el mercado no ha cerrado ({h:%H:%M} NY)"); return
    d = DATOS / h.strftime("%Y-%m-%d") / "grandes"
    tks = sorted({f["ticker"] for s in d.glob("snap-*.json") for f in json.loads(s.read_text()).get("filas", [])})
    if not tks:
        print("sin fotos de grandes hoy"); return
    vel = velas(tks + INDICES)
    (d / "velas").mkdir(exist_ok=True)
    for tk, v in vel.items():
        (d / "velas" / f"{tk}.json").write_text(json.dumps(v, separators=(",", ":")))
    print(f"cierre grandes: velas de {len(vel)} acciones")
    publicar("grandes: velas del día")


def publicar(msg: str) -> None:
    if not (DATOS / ".git").exists():
        return
    g = ["git", "-C", str(DATOS)]
    subprocess.run(g + ["add", "-A", "--", ":(glob)*/grandes/**"], check=False)
    if subprocess.run(g + ["diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(g + ["commit", "-qm", msg], check=False)
    for _ in range(4):
        subprocess.run(g + ["pull", "-q", "--rebase", "origin", "datos"], check=False)
        if subprocess.run(g + ["push", "-q", "origin", "HEAD:datos"]).returncode == 0:
            return
        time.sleep(5)


def resumen(s: dict) -> str:
    lin = [f"{s['hora_ny']} · {len(s['filas'])} filas · índices {s['indices_chg']} · {s['duracion_s']}s"]
    for f in s["filas"]:
        ne = f["noticias_eval"]
        lin.append(f"{f['ticker']:<6} {f['chg']:>6}%  cap {f['cap_B']}B  rvol {f['rvol']}/{f['rvol_hora']}h  atr {f['atr14_pct']}%  "
                   f"vwap {f['sobre_vwap_pct']}%  noticia {ne['estado']}: {(ne['validas'][0]['titulo'] if ne['validas'] else '')[:60]}")
    lin += [f"ERROR {e['ticker']}: {e['error']}" for e in s["errores"]]
    return "\n".join(lin)


def main(modo: str) -> None:
    if modo == "cierre":
        return cierre()
    if modo == "una_vez":
        s, v = foto(); guardar(s, v); print(resumen(s)); publicar("grandes: foto")
        return
    hoy = ahora_ny()
    ini = hoy.replace(hour=INICIO[0], minute=INICIO[1], second=0, microsecond=0)
    fin = hoy.replace(hour=FIN[0], minute=FIN[1], second=0, microsecond=0)
    if hoy.weekday() >= 5 or hoy > fin or (ini - hoy).total_seconds() > 30 * 60:
        print(f"fuera de la ventana ({hoy:%a %H:%M} NY)"); return
    if hoy < ini:
        time.sleep((ini - hoy).total_seconds())
    n = 0
    while ahora_ny() <= fin:
        t0 = time.time()
        try:
            s, v = foto(); guardar(s, v); print(resumen(s), flush=True); n += 1
            if n % 4 == 0:  # pocos commits: no gastar despliegues de Vercel
                publicar(f"grandes: fotos hasta {s['hora_ny'][11:16]}")
        except Exception:
            traceback.print_exc()
        time.sleep(max(0, PASO_MIN * 60 - (time.time() - t0)))
    publicar("grandes: fin de la ventana")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "una_vez")
