"""Fuentes gratis y sin claves, tomadas de cómo las llama OpenBB (Apache 2.0).

Origen: https://github.com/OpenBB-finance/OpenBB, commit fac2bd450fac5e0b69ca33f11c931f8b1b0b79fa,
openbb_platform/providers/{nasdaq,cboe,finra}. Se copiaron solo las URL, parámetros y nombres de campo;
el código es propio (sin la librería, sin caché en disco).

Cada función devuelve datos con su "fuente" o {"error": motivo}. Nunca un valor por defecto.
- nasdaq_movers(sesion): lista "most advanced" de Nasdaq.com (sesión pre_market, market o after_hours).
  OpenBB: models/market_movers.py → api.nasdaq.com/api/marketmovers?assetclass=STOCKS&exchangestatus=...
- cboe_velas_1m(tk): velas de 1 min del día en curso de Cboe ("delayed quotes", con atraso).
  OpenBB: cboe models/equity_historical.py → cdn.cboe.com/api/global/delayed_quotes/charts/intraday/{tk}.json
- finra_short(tks): interés en corto oficial (consolidado, dos veces al mes) de FINRA.
  OpenBB: finra models/equity_short_interest.py → POST api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
- nasdaq_screener(): todas las acciones de Nasdaq, NYSE y AMEX (país, capitalización, sector).
  OpenBB: nasdaq models/equity_screener.py → api.nasdaq.com/api/screener/stocks?download=true
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
SESIONES = {"market": "currentMarket", "pre_market": "premarket", "after_hours": "afterHours"}


def _get(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=data, headers=dict(UA, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null"), r.status
    except urllib.error.HTTPError as e:
        # El motivo que da el servidor (FINRA explica qué campo rechaza) vale más que solo el código.
        try:
            cuerpo = e.read().decode("utf-8", "replace")[:160]
        except Exception:
            cuerpo = ""
        raise RuntimeError(f"HTTP {e.code} {cuerpo}".strip()) from None


def _num(x):
    if x is None:
        return None
    s = str(x).replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip()
    if s in ("", "N/A", "--", "UNCH"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def nasdaq_movers(sesion: str = "pre_market", limite: int = 50) -> dict:
    hora = datetime.now(NY).isoformat(timespec="seconds")
    try:
        js, _ = _get(f"https://api.nasdaq.com/api/marketmovers?assetclass=STOCKS&exchangestatus={SESIONES[sesion]}&limit={limite}")
        bloque = ((js or {}).get("data") or {}).get("STOCKS") or {}
        tabla = (bloque.get("MostAdvanced") or {}).get("table") or {}
        filas = tabla.get("rows") or []
        if not filas:
            return {"fuente": "Nasdaq marketmovers", "sesion": sesion, "hora_ny": hora,
                    "error": "Nasdaq no publicó la lista 'MostAdvanced' para esta sesión",
                    "claves": sorted(bloque.keys())[:10]}
        out = []
        for f in filas:
            precio, cambio = _num(f.get("lastSalePrice")), _num(f.get("lastSaleChange"))
            pct = _num(f.get("change"))
            if pct is None and precio is not None and cambio is not None and precio - cambio:
                pct = cambio / (precio - cambio) * 100
            out.append({"ticker": f.get("symbol"), "nombre": f.get("name"), "precio": precio, "cambio": cambio,
                        "cambio_pct": None if pct is None else round(pct, 2)})
        return {"fuente": "Nasdaq marketmovers", "sesion": sesion, "hora_ny": hora,
                "titulo": tabla.get("asOf") or bloque.get("MostAdvanced", {}).get("title"), "filas": out}
    except Exception as e:
        return {"fuente": "Nasdaq marketmovers", "sesion": sesion, "hora_ny": hora, "error": f"{type(e).__name__}: {e}"[:200]}


def cboe_velas_1m(tk: str) -> dict:
    """{"fuente", "velas": [[iso, o, h, l, c, vol], ...]} del día en curso, o {"error"}."""
    try:
        url = f"https://cdn.cboe.com/api/global/delayed_quotes/charts/intraday/{tk.upper()}.json"
        try:
            js, _ = _get(url)
        except RuntimeError:
            # Segundo intento como lo pide su propio sitio (el CDN rechaza pedidos sin origen).
            js, _ = _get(url, headers={"Referer": "https://www.cboe.com/", "Origin": "https://www.cboe.com"})
        d = (js or {}).get("data") or []
        velas = []
        for x in d:
            p = x.get("price") or {}
            v = x.get("volume") or {}
            velas.append([x.get("datetime"), _num(p.get("open")), _num(p.get("high")), _num(p.get("low")),
                          _num(p.get("close")), int(_num(v.get("stock_volume", v.get("volume"))) or 0)])
        if not velas:
            return {"fuente": "Cboe delayed quotes (1 min, con atraso)", "error": "Cboe no trajo velas de hoy"}
        return {"fuente": "Cboe delayed quotes (1 min, con atraso)", "velas": velas}
    except Exception as e:
        return {"fuente": "Cboe delayed quotes (1 min, con atraso)", "error": f"{type(e).__name__}: {e}"[:200]}


def finra_short(tks: list[str]) -> dict:
    """{ticker: {"short", "fecha", "dias_cubrir", "fuente"}} con el último ciclo publicado, o {"error"}."""
    campos = ["settlementDate", "symbolCode", "currentShortPositionQuantity", "previousShortPositionQuantity",
              "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent", "revisionFlag", "stockSplitFlag"]
    syms = ["".join(c for c in t if c.isalnum()).upper() for t in tks]
    try:
        # Igual que OpenBB: sin sortFields (FINRA respondió 400 con él); se ordena aquí. Se acota a 120 días
        # para no pasar el tope de 5.000 filas; si FINRA rechaza el filtro de fecha, se pide sin él.
        url = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
        hdr = {"Accept": "application/json", "Content-Type": "application/json"}
        base = {"fields": campos, "domainFilters": [{"fieldName": "symbolCode", "values": syms}], "offset": 0, "limit": 5000}
        desde = (datetime.now(NY) - timedelta(days=120)).date().isoformat()
        hasta = datetime.now(NY).date().isoformat()
        try:
            filas, st = _get(url, data=json.dumps(dict(base, dateRangeFilters=[
                {"fieldName": "settlementDate", "startDate": desde, "endDate": hasta}])).encode(), headers=hdr)
        except RuntimeError as e:
            if "HTTP 400" not in str(e):
                raise
            filas, st = _get(url, data=json.dumps(base).encode(), headers=hdr)
        if st == 204 or not filas:
            return {"error": "FINRA no tiene interés en corto para esas acciones"}
        out: dict = {}
        for f in sorted(filas, key=lambda z: z.get("settlementDate") or "", reverse=True):
            s = f.get("symbolCode")
            if s in out:
                continue
            out[s] = {"short": f.get("currentShortPositionQuantity"), "anterior": f.get("previousShortPositionQuantity"),
                      "fecha": f.get("settlementDate"), "dias_cubrir": f.get("daysToCoverQuantity"),
                      "fuente": "FINRA (interés en corto consolidado)"}
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def nasdaq_screener() -> dict:
    """{"fuente", "filas": [{ticker, pais, cap, sector, ...}]} o {"error"}."""
    try:
        js, _ = _get("https://api.nasdaq.com/api/screener/stocks?download=true&tableonly=true", timeout=40)
        filas = (((js or {}).get("data") or {}).get("rows")) or []
        out = [{"ticker": f.get("symbol"), "nombre": f.get("name"), "pais": f.get("country") or None,
                "cap": _num(f.get("marketCap")), "sector": f.get("sector") or None, "precio": _num(f.get("lastsale")),
                "cambio_pct": _num(f.get("pctchange"))} for f in filas]
        if not out:
            return {"fuente": "Nasdaq screener", "error": "Nasdaq no trajo filas"}
        return {"fuente": "Nasdaq screener", "filas": out}
    except Exception as e:
        return {"fuente": "Nasdaq screener", "error": f"{type(e).__name__}: {e}"[:200]}
