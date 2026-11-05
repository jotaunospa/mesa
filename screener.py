"""Screener de la Mesa: réplica del criterio del scanner de GreenOrDieTrying + extras.

Criterio del scanner (descripción pública del canal, 2026-11-05):
  "The day's biggest movers with a float under 50 million shares" → top gainers
  por % del día, float < 50 M. No declara precio ni volumen mínimo.

Fuentes (gratis): Yahoo vía yfinance (no oficial), RSS de pausas de Nasdaq Trader
(oficial), EDGAR (oficial). Nada de Yahoo es verificado: el float es estimación
y va siempre en `unsure`. Los indicadores calculados se listan en `calc`.

Uso:
  python screener.py una_vez      → una foto ahora
  python screener.py ventana      → fotos cada PASO_MIN entre INICIO y FIN (hora NY)
Escribe en $DATOS (por defecto ./datos): AAAA-MM-DD/snap-HHMM.json y ultimo.json
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery as Q

from lib import sec_mesa

NY = ZoneInfo("America/New_York")
DATOS = Path(os.environ.get("DATOS", "datos"))

FLOAT_MAX = 50e6          # criterio del scanner
FLOAT_MUY_BAJO = 2e6      # el scanner los marca en amarillo
PRECIO_MAX = 20           # hipótesis (ver universo())
RANCIA_S = 900            # cotización con más de 15 min se descarta de la foto
MAX_FILAS = 20            # filas con detalle completo por foto
INICIO, FIN, PASO_MIN = (9, 31), (10, 20), 3   # ventana 09:35–10:15 NY + margen (antes de 9:30 Yahoo muestra el % de ayer)
PAISES_ROJOS = {"China", "Hong Kong", "Macau", "Macao", "Taiwan", "Singapore", "Malaysia"}

# Filtro propio de la Mesa (distinto del scanner): se marca, no se excluye.
MESA_PRECIO = (1, 20)
MESA_VOLM_MIN = 0.5

_cache_info: dict = {}
_cache_base: dict = {}
_cache_dil: dict = {}


# ───────────────────────── utilidades ─────────────────────────

def r(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n)


def ahora_ny() -> datetime:
    return datetime.now(NY)


def info(tk: str) -> dict:
    if tk not in _cache_info:
        try:
            _cache_info[tk] = yf.Ticker(tk).info or {}
        except Exception:
            _cache_info[tk] = {}
    return _cache_info[tk]


def solo_regular(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    t = df.index.tz_convert(NY)
    m = (t.hour * 60 + t.minute >= 9 * 60 + 30) & (t.hour * 60 + t.minute < 16 * 60)
    return df[m]


def base_10d(tk: str) -> dict:
    """Una vez por día y ticker: perfil de volumen acumulado por minuto del día
    (últimas sesiones, velas 5m), cierre/mínimo de cada día (para SSR) y splits."""
    if tk in _cache_base:
        return _cache_base[tk]
    out = {"perfil": {}, "dias": [], "splits_30d": [], "splits_12m": []}
    try:
        t = yf.Ticker(tk)
        h = solo_regular(t.history(period="15d", interval="5m", prepost=False))
        hoy = ahora_ny().date()
        if h is not None and not h.empty:
            idx = h.index.tz_convert(NY)
            h = h.assign(_d=idx.date, _m=idx.hour * 60 + idx.minute)
            previos = h[h["_d"] < hoy]
            # volumen acumulado a cada minuto del día, promedio entre días previos
            acum = {}
            for d, g in previos.groupby("_d"):
                c = g.sort_values("_m")
                acum[d] = dict(zip(c["_m"], c["Volume"].cumsum()))
                out["dias"].append({"fecha": str(d), "cierre": float(c["Close"].iloc[-1]),
                                    "minimo": float(c["Low"].min())})
            ult = sorted(acum)[-10:]
            for m in range(9 * 60 + 30, 16 * 60, 5):
                vals = [acum[d].get(m) for d in ult if acum[d].get(m) is not None]
                if vals:
                    out["perfil"][m] = sum(vals) / len(vals)
            out["dias"] = out["dias"][-10:]
        s = t.splits
        if s is not None and len(s):
            s.index = s.index.tz_convert(NY)
            rec = s[s.index >= pd.Timestamp.now(tz=NY) - pd.Timedelta(days=30)]
            out["splits_30d"] = [{"fecha": str(i.date()), "ratio": float(v)} for i, v in rec.items()]
            r12 = s[s.index >= pd.Timestamp.now(tz=NY) - pd.Timedelta(days=365)]
            out["splits_12m"] = [{"fecha": str(i.date()), "ratio": float(v)} for i, v in r12.items()]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    _cache_base[tk] = out
    return out


def dil(tk: str) -> dict:
    if tk not in _cache_dil:
        try:
            _cache_dil[tk] = sec_mesa.dilucion(tk)
        except Exception as e:
            _cache_dil[tk] = {"dil": None, "estado": f"error {type(e).__name__}: {e}"[:200], "dil_motivo": []}
    return _cache_dil[tk]


def confianza_float(flt, d: dict, b: dict) -> tuple[str, list]:
    """Diagnóstico 05-11 (10 tickers del scanner): en emisores de EE.UU. el float de
    Yahoo coincidió EXACTO con el scanner (LABT, AMTX, FLYE, PLCE). Las diferencias
    fueron todas emisores extranjeros con splits inversos repetidos (AEHL, NTCL, ZNB,
    MASK, JDZG): ahí ninguna fuente gratis es confiable, y la SEC tampoco ayuda porque
    presentan 20-F anual. Este nivel dice cuándo creerle al número."""
    motivos = []
    port = d.get("acciones_portada") or {}
    rs = [x for x in b.get("splits_12m", []) if x["ratio"] < 1]   # inversos
    if flt is None:
        return "sin_dato", ["Yahoo no informa float"]
    if d.get("extranjero") or d.get("estado") == "sin_cik":
        motivos.append("emisor extranjero o sin CIK: sin portada trimestral en la SEC")
    if rs:
        motivos.append(f"{len(rs)} split(s) inverso(s) en 12 meses (último {rs[-1]['fecha']})")
    if port.get("valor") and flt > port["valor"] * 1.02:
        motivos.append(f"float de Yahoo mayor que las acciones de la portada SEC ({port['valor']:,} al {port['fecha']})")
    if motivos:
        return "baja", motivos
    if not port.get("valor"):
        return "media", ["sin acciones de portada en la SEC para contrastar"]
    dias = (ahora_ny().date() - datetime.fromisoformat(port["fecha"]).date()).days
    if dias > 135:
        motivos.append(f"portada SEC de hace {dias} días")
    if d.get("ventas_tras_portada"):
        motivos.append(f"{d['ventas_tras_portada']} 424B después de la portada: puede haber más acciones")
    if motivos:
        return "media", motivos
    return "alta", [f"EE.UU., float ≤ acciones de portada SEC ({port['valor']:,} al {port['fecha']}, {port['form']})"]


# ───────────────────────── fuentes del día ─────────────────────────

def pausas_nasdaq() -> tuple[dict, str]:
    """RSS de Nasdaq Trader → {TICKER: [ {hora, codigo, reanuda} ]} solo de hoy (NY)."""
    hoy = ahora_ny().strftime("%m/%d/%Y")
    out: dict = {}
    try:
        req = urllib.request.Request("https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
                                     headers={"User-Agent": "Mozilla/5.0"})
        raiz = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
        ns = {"n": "http://www.nasdaqtrader.com/"}
        for it in raiz.iter("item"):
            sym = it.findtext("n:IssueSymbol", namespaces=ns)
            if not sym or it.findtext("n:HaltDate", namespaces=ns) != hoy:
                continue
            out.setdefault(sym, []).append({
                "hora": it.findtext("n:HaltTime", namespaces=ns),
                "codigo": it.findtext("n:ReasonCode", namespaces=ns),
                "reanuda": it.findtext("n:ResumptionTradeTime", namespaces=ns) or None})
        return out, "ok"
    except Exception as e:
        return out, f"falla: {type(e).__name__}"


def universo() -> list[dict]:
    q = Q("and", [Q("eq", ["region", "us"]),
                  Q("gte", ["percentchange", 5]),
                  # Sin precio mínimo: el scanner muestra acciones de $0,13 (05-11).
                  # Tope de $20: ECPG ($122) y EPAM ($164) subían >19% y el scanner no las
                  # mostraba (05-11). Es una HIPÓTESIS de 2 casos; revisar con más días.
                  Q("btwn", ["intradayprice", 0.05, PRECIO_MAX]),
                  Q("gte", ["dayvolume", 100_000])])
    res = yf.screen(q, sortField="percentchange", sortAsc=False, size=100)
    return res.get("quotes", [])


# ───────────────────────── una fila ─────────────────────────

def fila(qt: dict, halts: dict, vivo: dict, ahora: datetime) -> dict:
    tk = qt["symbol"]
    inf = info(tk)
    calc, unsure = [], ["floatM"]
    precio = qt.get("regularMarketPrice")
    prev = qt.get("regularMarketPreviousClose") or inf.get("previousClose")
    vol = qt.get("regularMarketVolume")
    avgv = qt.get("averageDailyVolume3Month") or inf.get("averageVolume")
    flt = inf.get("floatShares")
    hi, lo = qt.get("regularMarketDayHigh"), qt.get("regularMarketDayLow")

    # velas de 1 minuto de hoy, con premarket
    v1 = None
    try:
        v1 = yf.Ticker(tk).history(period="1d", interval="1m", prepost=True)
        if v1 is not None and not v1.empty:
            v1.index = v1.index.tz_convert(NY)
    except Exception:
        v1 = None
    reg = solo_regular(v1) if v1 is not None else None
    pre = None
    if v1 is not None and not v1.empty:
        mm = v1.index.hour * 60 + v1.index.minute
        pre = v1[mm < 9 * 60 + 30]

    vwap = None
    if reg is not None and not reg.empty and reg["Volume"].sum() > 0:
        tp = (reg["High"] + reg["Low"] + reg["Close"]) / 3
        vwap = float((tp * reg["Volume"]).sum() / reg["Volume"].sum())
        calc.append("vwap")

    surge5 = actividad = None
    if v1 is not None and len(v1) >= 6:
        surge5 = (float(v1["Close"].iloc[-1]) / float(v1["Close"].iloc[-6]) - 1) * 100
        calc.append("surge5m")
    if reg is not None and len(reg) >= 5 and reg["Volume"].iloc[:-1].mean() > 0:
        actividad = float(reg["Volume"].iloc[-1]) / float(reg["Volume"].iloc[:-1].mean())
        calc.append("actividad")

    velas15 = []
    if v1 is not None and not v1.empty:
        for i, row in v1.tail(15).iterrows():
            velas15.append([i.strftime("%H:%M"), r(row["Open"], 4), r(row["High"], 4),
                            r(row["Low"], 4), r(row["Close"], 4), int(row["Volume"])])

    b = base_10d(tk)
    rvol_hora = None
    m_ahora = ahora.hour * 60 + ahora.minute
    if b["perfil"] and reg is not None and not reg.empty and m_ahora >= 9 * 60 + 35:
        m5 = min(max((m_ahora // 5) * 5, 9 * 60 + 30), 15 * 60 + 55)
        tip = b["perfil"].get(m5)
        if tip:
            rvol_hora = float(reg["Volume"].sum()) / tip
            calc.append("rvol_hora")
    if rvol_hora is None:
        unsure.append("rvol_hora")

    # SSR: hoy si el mínimo cayó ≥10% bajo el cierre previo; o se arrastra de ayer.
    ssr, ssr_motivo = False, None
    if lo and prev and lo <= prev * 0.9:
        ssr, ssr_motivo = True, "hoy: mínimo ≥10% bajo el cierre anterior"
    elif len(b["dias"]) >= 2:
        ay, anteay = b["dias"][-1], b["dias"][-2]
        if ay["minimo"] <= anteay["cierre"] * 0.9:
            ssr, ssr_motivo = True, "arrastrado de ayer"
    calc.append("ssr")

    h_hoy = halts.get(tk, [])
    dir_halts = []
    for h in h_hoy:
        d = None
        try:
            hh, mi, *_ = [int(x) for x in h["hora"].split(":")]
            antes = v1[(v1.index.hour * 60 + v1.index.minute) <= hh * 60 + mi]
            if len(antes) >= 6:
                d = "arriba" if antes["Close"].iloc[-1] >= antes["Close"].iloc[-6] else "abajo"
        except Exception:
            pass
        dir_halts.append({**h, "direccion": d})
    en_pausa = any(h.get("reanuda") in (None, "") for h in h_hoy)

    noticias = []
    try:
        for n in (yf.Ticker(tk).news or [])[:3]:
            c = n.get("content", n)
            noticias.append({"titulo": c.get("title"),
                             "fecha": c.get("pubDate") or c.get("providerPublishTime"),
                             "fuente": (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else None})
    except Exception:
        unsure.append("news")

    d = dil(tk)
    conf_float, conf_motivos = confianza_float(flt, d, b)
    if conf_float == "alta":
        unsure.remove("floatM")
    bid, ask = inf.get("bid"), inf.get("ask")
    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if bid and ask and ask > bid > 0 else None
    if spread is None:
        unsure.append("spread_pct")
    pais = inf.get("country")
    tq = qt.get("regularMarketTime")
    chg = qt.get("regularMarketChangePercent")
    volM = vol / 1e6 if vol else None
    en_mesa = (precio is not None and MESA_PRECIO[0] <= precio <= MESA_PRECIO[1]
               and volM is not None and volM >= MESA_VOLM_MIN)

    return {
        # ── campos de la Mesa ──
        "ticker": tk,
        "price": r(precio, 4),
        "chg": r(chg),
        "volM": r(volM, 3),
        "rvol": r(vol / avgv) if vol and avgv else None,   # contra un día normal (como el scanner)
        "floatM": r(flt / 1e6, 3) if flt else None,
        "dayHigh": r(hi, 4), "dayLow": r(lo, 4),
        "halt": en_pausa,
        "news": bool(noticias),
        "headline": noticias[0]["titulo"] if noticias else None,
        "dil": d.get("dil"),
        "unsure": sorted(set(unsure + (["rvol"] if not (vol and avgv) else []))),
        # ── extras del scanner ──
        "rvol_hora": r(rvol_hora),
        "actividad": r(actividad),
        "rotaciones": r(vol / flt) if vol and flt else None,
        "float_muy_bajo": bool(flt and flt < FLOAT_MUY_BAJO),
        "float_confianza": conf_float, "float_motivos": conf_motivos,
        "acciones_portada_sec": d.get("acciones_portada"),
        "split_inverso_12m": [x for x in b.get("splits_12m", []) if x["ratio"] < 1],
        "hod_dist_pct": r((precio / hi - 1) * 100) if precio and hi else None,
        "vwap": r(vwap, 4),
        "sobre_vwap_pct": r((precio / vwap - 1) * 100) if precio and vwap else None,
        "surge5m": r(surge5),
        "velas15": velas15,
        "ssr": ssr, "ssr_motivo": ssr_motivo,
        "short_pct_float": r((inf.get("shortPercentOfFloat") or 0) * 100) if inf.get("shortPercentOfFloat") else None,
        "pais": pais, "pais_riesgo": pais in PAISES_ROJOS,
        "split_reciente": b["splits_30d"],
        "halts_hoy": dir_halts,
        "noticias": noticias,
        # ── extras propios ──
        "premarket": {"max": r(pre["High"].max(), 4), "vol": int(pre["Volume"].sum())}
                     if pre is not None and not pre.empty else None,
        "spread_pct": r(spread),
        "sec": {k: d.get(k) for k in ("dil_motivo", "veto_venta", "veto_quiebra", "extranjero",
                                      "runway_q", "caja_periodo", "filings_recientes", "estado")},
        "sec_hoy": vivo.get(tk, []),
        "veto": bool(d.get("veto_venta") or d.get("veto_quiebra") or any(
            f["form"].startswith("424B") for f in vivo.get(tk, []))),
        "en_filtro_mesa": en_mesa,
        "calc": calc,
        "meta": {"src": "yahoo-gratis", "edad_s": int(time.time() - tq) if tq else None,
                 "bolsa": qt.get("fullExchangeName")},
    }


# ───────────────────────── temperatura ─────────────────────────

def temperatura(filas: list, halts: dict) -> dict:
    fuertes = [f for f in filas if (f["chg"] or 0) >= 20]
    try:
        iwm = yf.Ticker("IWM").fast_info
        iwm_chg = (iwm["lastPrice"] / iwm["previousClose"] - 1) * 100
    except Exception:
        iwm_chg = None
    n = len(fuertes)
    # Umbrales provisionales: se recalibran con el registro de resultados.
    nivel = "caliente" if n >= 5 else "tibia" if n >= 2 else "fría"
    return {"nivel": nivel, "umbral": "provisional (≥5 caliente, 2–4 tibia, ≤1 fría)",
            "suben_20_float_bajo": n, "iwm_chg": r(iwm_chg),
            "pausas_hoy_total": sum(len(v) for v in halts.values())}


# ───────────────────────── una foto ─────────────────────────

def foto() -> dict:
    t0 = time.time()
    ahora = ahora_ny()
    halts, estado_halts = pausas_nasdaq()
    try:
        vivo = sec_mesa.filings_en_vivo()
        estado_vivo = "ok"
    except Exception as e:
        vivo, estado_vivo = {}, f"falla: {type(e).__name__}"
    quotes = universo()
    # Derechos, warrants y unidades (5 letras terminadas en R/W/U en Nasdaq) no son acciones.
    quotes = [q for q in quotes if not (len(q["symbol"]) >= 5 and q["symbol"][-1] in "RWU")]
    # float < 50 M (el float es una estimación de Yahoo)
    candidatas = []
    for qt in quotes:
        flt = info(qt["symbol"]).get("floatShares")
        if flt is None or flt < FLOAT_MAX:
            candidatas.append(qt)
    # Cotizaciones rancias: el screener de Yahoo a veces devuelve precios de días atrás.
    rancias = [qt["symbol"] for qt in candidatas
               if qt.get("regularMarketTime") and time.time() - qt["regularMarketTime"] > RANCIA_S]
    candidatas = [qt for qt in candidatas if qt["symbol"] not in rancias]
    filas, errores = [], []
    for qt in candidatas[:MAX_FILAS]:
        try:
            filas.append(fila(qt, halts, vivo, ahora))
        except Exception as e:
            errores.append({"ticker": qt.get("symbol"), "error": f"{type(e).__name__}: {e}"[:200]})
    sin_float = [f["ticker"] for f in filas if f["floatM"] is None]
    return {
        "hora_ny": ahora.isoformat(timespec="seconds"),
        "criterio": "top gainers del día, float < 50 M (scanner GreenOrDieTrying); "
                    "float desconocido se incluye y queda en unsure",
        "universo_yahoo": len(quotes), "con_float_bajo": len(candidatas),
        "sin_float_conocido": sin_float,
        "descartadas_rancias": rancias,
        "fuentes": {"pausas_nasdaq": estado_halts, "sec_en_vivo": estado_vivo},
        "temperatura": temperatura(filas, halts),
        "filas": filas, "errores": errores,
        "duracion_s": round(time.time() - t0, 1),
    }


def guardar(snap: dict) -> Path:
    h = datetime.fromisoformat(snap["hora_ny"])
    d = DATOS / h.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"snap-{h.strftime('%H%M')}.json"
    txt = json.dumps(snap, ensure_ascii=False, indent=1, default=str)
    p.write_text(txt)
    (d / "ultimo.json").write_text(txt)
    return p


def publicar(msg: str) -> None:
    """Si $DATOS es un clon git (rama datos), commit + push. Si no, nada."""
    if not (DATOS / ".git").exists():
        return
    g = ["git", "-C", str(DATOS)]
    subprocess.run(g + ["add", "-A"], check=False)
    if subprocess.run(g + ["diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(g + ["commit", "-qm", msg], check=False)
    for _ in range(3):
        if subprocess.run(g + ["push", "-q", "origin", "HEAD:datos"]).returncode == 0:
            return
        subprocess.run(g + ["pull", "-q", "--rebase", "origin", "datos"], check=False)


def resumen(snap: dict) -> str:
    t = snap["temperatura"]
    lin = [f"{snap['hora_ny']} · {snap['con_float_bajo']}/{snap['universo_yahoo']} con float<50M · "
           f"temp {t['nivel']} ({t['suben_20_float_bajo']} ≥20%, IWM {t['iwm_chg']}%) · "
           f"pausas {snap['fuentes']['pausas_nasdaq']} · SEC vivo {snap['fuentes']['sec_en_vivo']} · {snap['duracion_s']}s"]
    for f in snap["filas"]:
        lin.append(f"{f['ticker']:<6} ${f['price']:<8} {f['chg']:>7}%  vol {f['volM']}M  rvol {f['rvol']}/{f['rvol_hora']}h  "
                   f"float {f['floatM']}M[{f['float_confianza']}] rot {f['rotaciones']}  vwap {f['sobre_vwap_pct']}%  hod {f['hod_dist_pct']}%  "
                   f"surge {f['surge5m']}  ssr {f['ssr']}  dil {f['dil']}{' VETO' if f['veto'] else ''}  "
                   f"halt {len(f['halts_hoy'])}  edad {f['meta']['edad_s']}s")
    for e in snap["errores"]:
        lin.append(f"ERROR {e['ticker']}: {e['error']}")
    return "\n".join(lin)


def main(modo: str) -> None:
    if modo == "una_vez":
        s = foto(); p = guardar(s); print(resumen(s)); publicar(f"foto {p.name}")
        return
    hoy = ahora_ny()
    ini = hoy.replace(hour=INICIO[0], minute=INICIO[1], second=0, microsecond=0)
    fin = hoy.replace(hour=FIN[0], minute=FIN[1], second=0, microsecond=0)
    if hoy.weekday() >= 5:
        print("fin de semana: nada que hacer"); return
    if hoy > fin:
        print(f"ventana ya cerró ({hoy:%H:%M} NY)"); return
    espera = (ini - hoy).total_seconds()
    if espera > 30 * 60:
        print(f"muy temprano ({hoy:%H:%M} NY): sale y deja que corra el respaldo"); return
    if espera > 0:
        print(f"esperando {int(espera)}s hasta {ini:%H:%M} NY"); time.sleep(espera)
    while ahora_ny() <= fin:
        t0 = time.time()
        try:
            s = foto(); p = guardar(s); print(resumen(s), flush=True); publicar(f"foto {p.name}")
        except Exception:
            traceback.print_exc()
        time.sleep(max(0, PASO_MIN * 60 - (time.time() - t0)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "una_vez")
