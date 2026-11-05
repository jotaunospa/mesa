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

# Criterio de la lista, deducido comparando contra el scanner del canal el 05-11 (3 rondas,
# 33 decisiones, 33 aciertos). La descripción del canal dice "float < 50 M", pero en la práctica
# muestra DNUT (91 M) y AMTX (67 M) por su volumen. Umbrales aproximados: se ajustan con más rondas.
FLOAT_MAX = 15e6          # float bajo (entre 8,1 M que entra y 19,3 M que no)
VOL_ALTO = 14e6           # o volumen alto (entre 13,1 M que no entra y 15,5 M que sí)
VOL_MIN = 1e6             # volumen mínimo (0,23 M no entra; 1,44 M sí)
FLOAT_MUY_BAJO = 2e6      # el scanner los marca en amarillo
PRECIO_MAX = 20           # tope de precio: AEHL ($18,65) entra; APP (cientos de USD, +31%, vol 20 M) no (05-11 15:46)
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
_velas_vivo: dict = {}   # velas de 1 min del día por ticker, para el gráfico del dashboard


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


CONCUERDA = 0.15   # dos fuentes de float "concuerdan" si difieren menos de 15%


def float_cruzado(f_yahoo, tv: dict, d: dict, b: dict) -> dict:
    """Cruza el float de Yahoo con el de TradingView y con la portada SEC.

    confirmado : dos fuentes independientes concuerdan (±15%), o emisor de EE.UU.
                 con float que cuadra con una portada SEC reciente (confianza_float alta)
    en_disputa : dos fuentes difieren más de 15% → se informa el rango [mín, máx]
    una_fuente : solo hay un número
    sin_dato   : nadie informa float
    El dictamen evalúa las reglas con los DOS extremos del rango: si el resultado
    es el mismo, la disputa no importa para decidir.
    """
    f_tv = tv.get("float")
    vals = [v for v in (f_yahoo, f_tv) if v]
    nivel_sec, motivos_sec = confianza_float(f_yahoo, d, b)
    out = {"yahoo_M": r(f_yahoo / 1e6, 3) if f_yahoo else None,
           "tradingview_M": r(f_tv / 1e6, 3) if f_tv else None,
           "sec_portada": d.get("acciones_portada"), "chequeo_sec": nivel_sec, "motivos_sec": motivos_sec}
    if not vals:
        out.update(estado="sin_dato", rango_M=None)
    elif len(vals) == 1:
        out.update(estado="confirmado" if nivel_sec == "alta" else "una_fuente", rango_M=[r(vals[0] / 1e6, 3)] * 2)
    else:
        lo, hi = min(vals), max(vals)
        acuerdo = (hi - lo) / hi <= CONCUERDA
        out.update(estado="confirmado" if (acuerdo or nivel_sec == "alta") else "en_disputa",
                   rango_M=[r(lo / 1e6, 3), r(hi / 1e6, 3)])
    return out


def confianza_float(flt, d: dict, b: dict) -> tuple[str, list]:
    """Diagnóstico 05-11 (10 tickers del scanner): en emisores de EE.UU. el float de
    Yahoo coincidió EXACTO con el scanner (LABT, AMTX, FLYE, PLCE). Las diferencias
    fueron todas emisores extranjeros con splits inversos repetidos (AEHL, NTCL, ZNB,
    MASK, JDZG): ahí ninguna fuente gratis es confiable, y la SEC tampoco ayuda porque
    presentan 20-F anual. Este nivel dice cuándo creerle al número."""
    motivos = []
    port = d.get("acciones_portada") or {}
    rs = [x for x in b.get("splits_12m", []) if x["ratio"] < 1]   # inversos
    # Un split anterior a la portada SEC ya está incluido en ese conteo: no resta confianza.
    if port.get("fecha") and not (d.get("extranjero") or d.get("estado") == "sin_cik"):
        rs = [x for x in rs if x["fecha"] > port["fecha"]]
    if flt is None:
        return "sin_dato", ["Yahoo no informa float"]
    if d.get("extranjero") or d.get("estado") == "sin_cik":
        motivos.append("emisor extranjero o sin CIK: sin portada trimestral en la SEC")
    if rs:
        motivos.append(f"{len(rs)} split(s) inverso(s) en 12 meses sin portada SEC posterior (último {rs[-1]['fecha']})")
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


_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/130.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
_cache_tv: dict = {}


def float_tradingview(tickers: list) -> dict:
    """Segunda fuente de float: scanner público de TradingView (otro proveedor que Yahoo).
    Su precio viene con 15 min de atraso, así que de TradingView solo se usa el float."""
    faltan = [t for t in tickers if t not in _cache_tv]
    if faltan:
        try:
            body = json.dumps({"markets": ["america"], "symbols": {"query": {"types": []}, "tickers": []},
                               "filter": [{"left": "name", "operation": "in_range", "right": faltan}],
                               "columns": ["name", "float_shares_outstanding", "total_shares_outstanding"],
                               "range": [0, 200]}).encode()
            req = urllib.request.Request("https://scanner.tradingview.com/america/scan", data=body,
                                         headers=dict(_UA, **{"Content-Type": "application/json"}))
            js = json.loads(urllib.request.urlopen(req, timeout=20).read())
            for row in js.get("data", []):
                n, fl, tot = row["d"]
                _cache_tv.setdefault(n, {"float": fl, "acciones": tot})
        except Exception:
            pass
        for t in faltan:
            _cache_tv.setdefault(t, {"float": None, "acciones": None})
    return {t: _cache_tv.get(t, {}) for t in tickers}


def cotizacion_nasdaq(tk: str) -> dict:
    """Bid/ask y último precio desde Nasdaq.com (el bid/ask de Yahoo es basura en small caps)."""
    try:
        req = urllib.request.Request(f"https://api.nasdaq.com/api/quote/{tk}/info?assetclass=stocks",
                                     headers=dict(_UA, Origin="https://www.nasdaq.com", Referer="https://www.nasdaq.com/"))
        p = (json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data") or {}).get("primaryData") or {}
        num = lambda x: float(str(x).replace("$", "").replace(",", "")) if x not in (None, "", "N/A") else None
        return {"precio": num(p.get("lastSalePrice")), "bid": num(p.get("bidPrice")), "ask": num(p.get("askPrice")),
                "hora": p.get("lastTradeTimestamp")}
    except Exception as e:
        return {"error": type(e).__name__}


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
            _velas_vivo[tk] = velas_compactas(v1)
    except Exception:
        v1 = None
    reg = solo_regular(v1) if v1 is not None else None
    pre = None
    if v1 is not None and not v1.empty:
        mm = v1.index.hour * 60 + v1.index.minute
        pre = v1[mm < 9 * 60 + 30]
    # El scanner del canal mide el HOD y el mínimo incluyendo el premarket (comparado el 05-11:
    # ZNB 1,83 y FLYE 3,36 = máximos de premarket). Se guardan ambos.
    hi_reg, lo_reg = hi, lo
    if pre is not None and not pre.empty:
        hi = max(x for x in (hi, float(pre["High"].max())) if x is not None)
        lo = min(x for x in (lo, float(pre["Low"].min())) if x is not None)

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
    if lo_reg and prev and lo_reg <= prev * 0.9:   # SSR con la sesión regular
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
    fx = float_cruzado(flt, float_tradingview([tk])[tk], d, b)
    if fx["estado"] == "confirmado":
        unsure.remove("floatM")
    nq = cotizacion_nasdaq(tk)
    bid, ask = nq.get("bid"), nq.get("ask")
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
        "dayHigh": r(hi, 4), "dayLow": r(lo, 4),   # con premarket, como el scanner
        "dayHigh_regular": r(hi_reg, 4), "dayLow_regular": r(lo_reg, 4),
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
        "float_cruce": fx,
        "nasdaq": nq,
        "precio_cuadra": (abs(nq["precio"] / precio - 1) <= 0.03) if nq.get("precio") and precio else None,
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


# ───────────────────────── dictamen automático ─────────────────────────
# Regla profesional: lo que no se puede verificar no se opera. El sistema no le
# pide a José revisar nada a mano: decide con criterios fijos y explica por qué.

FLOAT_MIN_MESA = 1e6      # regla v1.2
EDAD_MAX_S = 120          # precio con más de 2 min de atraso no sirve para entrar
SPREAD_MAX = 3.0          # % ; más ancho se come la ganancia en papel y en real
PAUSA_RECIENTE_MIN = 15   # regla v1.2: pausa reciente = veto


def _min_desde(hhmmss: str, ahora: datetime):
    try:
        h, m, *_ = [int(x) for x in hhmmss.split(":")]
        return ahora.hour * 60 + ahora.minute - (h * 60 + m)
    except Exception:
        return None


def dictamen(f: dict, ahora: datetime) -> dict:
    no, ojo = [], []
    fx = f["float_cruce"]
    rng = fx.get("rango_M")
    fuentes = f"Yahoo {fx['yahoo_M']} M / TradingView {fx['tradingview_M']} M"
    if not rng:
        no.append("ninguna fuente informa float")
    else:
        bajo = [v * 1e6 < FLOAT_MIN_MESA for v in rng]
        if all(bajo):
            no.append(f"float bajo el mínimo de 1 M de la Mesa ({fuentes})")
        elif any(bajo):
            no.append(f"float en disputa y la regla de 1 M cambia según la fuente ({fuentes})")
        elif fx["estado"] == "en_disputa":
            ojo.append(f"float en disputa ({fuentes}), pero ambas cumplen las reglas")
        elif fx["estado"] == "una_fuente":
            ojo.append(f"float de una sola fuente ({fuentes}; " + "; ".join(fx["motivos_sec"]) + ")")
    if f["veto"]:
        det = "; ".join(m for m in (f["sec"].get("dil_motivo") or []) if "VETO" in m) or "424B presentado hoy"
        no.append(f"veto SEC: {det}")
    if f["dil"] == "alta":
        no.append("dilución alta según la SEC")
    if f["halt"]:
        no.append("en pausa ahora")
    for h in f["halts_hoy"]:
        m = _min_desde(h.get("reanuda") or "", ahora)
        if m is not None and 0 <= m < PAUSA_RECIENTE_MIN:
            no.append(f"pausa reanudada hace {m} min"); break
    if not f["en_filtro_mesa"]:
        no.append("fuera del filtro de la Mesa (1–20 USD, volumen ≥ 0,5 M)")
    edad = (f.get("meta") or {}).get("edad_s")
    if edad is None or edad > EDAD_MAX_S:
        no.append(f"sin operaciones hace {edad} s: poco líquida para entrar")
    if f["spread_pct"] is not None and f["spread_pct"] > SPREAD_MAX:
        no.append(f"spread {f['spread_pct']}% en Nasdaq (> {SPREAD_MAX}%): entrar y salir cuesta demasiado")
    # cautelas: no bloquean, pero el sistema las muestra y las cuenta en el registro
    if f["dil"] == "media":
        ojo.append("dilución media")
    if f["ssr"]:
        ojo.append("SSR activo (no se puede vender en corto a la baja)")
    if f["pais_riesgo"]:
        ojo.append(f"país de riesgo ({f['pais']})")
    if f["split_reciente"]:
        ojo.append("split en los últimos 30 días")
    # Spread desde Nasdaq.com. El de Yahoo es basura en small caps (05-11: ARQ 62% en
    # Yahoo, 0,85% en Nasdaq), por eso ya no se usa.
    if f["spread_pct"] is None:
        ojo.append("spread desconocido (Nasdaq no respondió)")
    if f.get("precio_cuadra") is False:
        ojo.append(f"precio de Yahoo y Nasdaq difieren más de 3% (Yahoo {f['price']}, Nasdaq {f['nasdaq'].get('precio')})")
    if f["rvol_hora"] is None:
        ojo.append("rvol por hora no calculable")
    apto = not no
    lectura = ("APTA para evaluar con las reglas de la Mesa." if apto else
               "NO APTA: " + no[0] + ("" if len(no) == 1 else f" (y {len(no) - 1} motivo(s) más)."))
    return {"apto": apto, "no_apto_por": no, "cautela": ojo, "lectura": lectura}


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
    # Quinta letra en Nasdaq: P preferente, R derecho, U unidad, W warrant, Z otros. No son acciones comunes.
    quotes = [q for q in quotes if not (len(q["symbol"]) >= 5 and q["symbol"][-1] in "PRUWZ")]
    # Sin OTC (el scanner no los muestra).
    quotes = [q for q in quotes if "OTC" not in (q.get("fullExchangeName") or "")]
    tv = float_tradingview([q["symbol"] for q in quotes])
    candidatas = []
    for qt in quotes:
        vals = [v for v in (info(qt["symbol"]).get("floatShares"), tv.get(qt["symbol"], {}).get("float")) if v]
        vol = qt.get("regularMarketVolume") or 0
        if vol >= VOL_MIN and ((not vals or min(vals) < FLOAT_MAX) or vol >= VOL_ALTO):
            candidatas.append(qt)
    # Cotizaciones rancias: el screener de Yahoo a veces devuelve precios de días atrás.
    rancias = [qt["symbol"] for qt in candidatas
               if qt.get("regularMarketTime") and time.time() - qt["regularMarketTime"] > RANCIA_S]
    candidatas = [qt for qt in candidatas if qt["symbol"] not in rancias]
    # Universo completo (sin tope de float), para comparar contra el scanner y deducir su criterio.
    universo_completo = []
    for qt in quotes:
        sym = qt["symbol"]
        universo_completo.append({
            "ticker": sym, "chg": r(qt.get("regularMarketChangePercent")), "price": r(qt.get("regularMarketPrice"), 4),
            "volM": r((qt.get("regularMarketVolume") or 0) / 1e6, 3),
            "float_yahoo_M": r((info(sym).get("floatShares") or 0) / 1e6, 3) or None,
            "float_tv_M": r((tv.get(sym, {}).get("float") or 0) / 1e6, 3) or None,
            "bolsa": qt.get("fullExchangeName"), "pais": info(sym).get("country"),
            "en_lista": sym in {q["symbol"] for q in candidatas[:MAX_FILAS]}})
    filas, errores = [], []
    for qt in candidatas[:MAX_FILAS]:
        try:
            fl = fila(qt, halts, vivo, ahora)
            fl.update(dictamen(fl, ahora))
            filas.append(fl)
        except Exception as e:
            errores.append({"ticker": qt.get("symbol"), "error": f"{type(e).__name__}: {e}"[:200]})
    sin_float = [f["ticker"] for f in filas if f["floatM"] is None]
    return {
        "hora_ny": ahora.isoformat(timespec="seconds"),
        "criterio": "top gainers del día sin OTC, volumen ≥ 1 M y (float < 15 M o volumen ≥ 14 M); "
                    "deducido del scanner GreenOrDieTrying el 05-11; float desconocido se incluye y queda en unsure",
        "universo_yahoo": len(quotes), "con_float_bajo": len(candidatas),
        "sin_float_conocido": sin_float,
        "descartadas_rancias": rancias,
        "universo_completo": universo_completo,
        "fuentes": {"pausas_nasdaq": estado_halts, "sec_en_vivo": estado_vivo},
        "temperatura": temperatura(filas, halts),
        "filas": filas, "errores": errores,
        "duracion_s": round(time.time() - t0, 1),
    }


def velas_compactas(v) -> list:
    """[hora "HH:MM", open, high, low, close, volumen] — formato del endpoint de velas."""
    return [[i.strftime("%H:%M"), r(x.Open, 4), r(x.High, 4), r(x.Low, 4), r(x.Close, 4), int(x.Volume)]
            for i, x in v.iterrows()]


def escribir_indice() -> None:
    """index.json en la raíz de los datos: el punto de entrada de los endpoints."""
    dias = sorted(p.name for p in DATOS.glob("20??-??-??") if p.is_dir())
    info_dias = []
    for d in dias:
        snaps = sorted(x.stem.replace("snap-", "") for x in (DATOS / d).glob("snap-*.json"))
        info_dias.append({"fecha": d, "fotos": snaps, "resultados": (DATOS / d / "resultados.json").exists()})
    (DATOS / "index.json").write_text(json.dumps({
        "version": 1, "actualizado_ny": ahora_ny().isoformat(timespec="seconds"),
        "ultimo_dia": dias[-1] if dias else None, "dias": info_dias}, ensure_ascii=False, indent=1))


def guardar(snap: dict) -> Path:
    h = datetime.fromisoformat(snap["hora_ny"])
    d = DATOS / h.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"snap-{h.strftime('%H%M')}.json"
    txt = json.dumps(snap, ensure_ascii=False, indent=1, default=str)
    p.write_text(txt)
    (d / "ultimo.json").write_text(txt)
    vv = d / "velas_vivo"; vv.mkdir(exist_ok=True)
    for f in snap["filas"]:
        if f["ticker"] in _velas_vivo:
            (vv / f"{f['ticker']}.json").write_text(json.dumps(_velas_vivo[f["ticker"]], separators=(",", ":")))
    escribir_indice()
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
                   f"float {f['float_cruce']['rango_M']}[{f['float_cruce']['estado']}] rot {f['rotaciones']}  vwap {f['sobre_vwap_pct']}%  hod {f['hod_dist_pct']}%  "
                   f"surge {f['surge5m']}  ssr {f['ssr']}  dil {f['dil']}{' VETO' if f['veto'] else ''}  "
                   f"halt {len(f['halts_hoy'])}  edad {f['meta']['edad_s']}s  → {f['lectura']}")
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
