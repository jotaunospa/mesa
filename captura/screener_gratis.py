"""Prueba del screener gratis: ¿Yahoo (vía yfinance) + Nasdaq Trader entregan los campos de la Mesa?

Nada de aquí es verificado: Yahoo es una fuente no oficial y el float es una estimación.
Cada fila trae `fuente` y `edad_s` (segundos desde la última cotización) para medir el atraso.

Salida: resultados/screener.json y resultados/resumen.txt
"""
import json, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf
from yfinance import EquityQuery as Q

OUT = Path("resultados"); OUT.mkdir(exist_ok=True)
ahora = time.time()

# Filtro de la Mesa: precio 1–20 USD, volumen >= 0,5 M, sube >= 10% hoy, EE.UU.
q = Q("and", [
    Q("eq", ["region", "us"]),
    Q("btwn", ["intradayprice", 1, 20]),
    Q("gte", ["dayvolume", 500_000]),
    Q("gte", ["percentchange", 10]),
])
res = yf.screen(q, sortField="percentchange", sortAsc=False, size=50)
quotes = res.get("quotes", [])

# Pausas: RSS público de Nasdaq Trader
halts = {}
try:
    req = urllib.request.Request("https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
                                 headers={"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
    ns = {"ndaq": "http://www.nasdaqtrader.com/"}
    for it in root.iter("item"):
        sym = it.findtext("ndaq:IssueSymbol", namespaces=ns)
        if sym:
            halts.setdefault(sym, []).append({
                "hora": it.findtext("ndaq:HaltTime", namespaces=ns),
                "codigo": it.findtext("ndaq:ReasonCode", namespaces=ns),
                "reanuda": it.findtext("ndaq:ResumptionTradeTime", namespaces=ns)})
    halts_ok = True
except Exception as e:
    halts_ok = f"falla: {e}"

filas = []
for qt in quotes[:15]:
    t = qt["symbol"]
    unsure = ["floatM"]  # el float siempre es estimación de Yahoo
    try:
        info = yf.Ticker(t).info
    except Exception:
        info = {}
    flt = info.get("floatShares")
    avgv = qt.get("averageDailyVolume3Month") or info.get("averageVolume")
    vol = qt.get("regularMarketVolume")
    rvol = round(vol / avgv, 2) if vol and avgv else None
    if rvol is None: unsure.append("rvol")
    noticias = []
    try:
        for n in (yf.Ticker(t).news or [])[:2]:
            c = n.get("content", n)
            noticias.append({"titulo": c.get("title"), "fecha": c.get("pubDate") or c.get("providerPublishTime")})
    except Exception:
        unsure.append("news")
    tq = qt.get("regularMarketTime")
    filas.append({
        "ticker": t,
        "price": qt.get("regularMarketPrice"),
        "chg": round(qt.get("regularMarketChangePercent", 0), 2),
        "volM": round(vol / 1e6, 3) if vol else None,
        "rvol": rvol,  # calculado: volumen de hoy / promedio 3 meses
        "floatM": round(flt / 1e6, 3) if flt else None,
        "dayHigh": qt.get("regularMarketDayHigh"),
        "dayLow": qt.get("regularMarketDayLow"),
        "halt": t in halts, "halts": halts.get(t, []),
        "news": bool(noticias), "headline": noticias[0]["titulo"] if noticias else None,
        "unsure": unsure,
        "meta": {"src": "yahoo-gratis", "edad_s": int(ahora - tq) if tq else None,
                 "bolsa": qt.get("fullExchangeName")},
    })

informe = {"hora_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "total_screener": res.get("total"), "halts_rss": halts_ok, "filas": filas}
(OUT / "screener.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2))
lineas = [f"{informe['hora_utc']} · screener={informe['total_screener']} · RSS pausas: {halts_ok} ({len(halts)} símbolos)"]
for f in filas:
    lineas.append(f"{f['ticker']:<6} ${f['price']:<7} {f['chg']:>7}%  vol {f['volM']}M  rvol {f['rvol']}  float {f['floatM']}M  "
                  f"halt {f['halt']}  edad {f['meta']['edad_s']}s  {(f['headline'] or '')[:60]}")
print("\n".join(lineas))
(OUT / "resumen.txt").write_text("\n".join(lineas))
