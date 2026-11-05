"""Diagnóstico de fuentes: ¿hay una segunda fuente gratis para cruzar float, spread y precio?
Compara Yahoo, TradingView (scanner público), Finviz y Nasdaq.com contra el scanner del canal."""
import json, re, sys, time, urllib.request
sys.path.insert(0, ".")
import yfinance as yf

SCANNER = {"LABT": 0.848, "AEHL": 0.677, "NTCL": 1.70, "ZNB": 4.55, "AMTX": 67.16, "FLYE": 1.56,
           "PLCE": 8.09, "BDMD": 6.50, "MASK": 0.646, "JDZG": 4.85, "ARQ": None, "DTIL": None, "TBI": None}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json, text/html", "Accept-Language": "en-US,en;q=0.9"}

def get(url, data=None, extra=None):
    h = dict(UA, **(extra or {}))
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

out = {"tv": {}, "finviz": {}, "nasdaq": {}, "yahoo": {}, "errores": []}
# TradingView
try:
    body = json.dumps({"markets": ["america"], "symbols": {"query": {"types": []}, "tickers": []},
                       "filter": [{"left": "name", "operation": "in_range", "right": list(SCANNER)}],
                       "columns": ["name", "exchange", "close", "change", "volume", "float_shares_outstanding",
                                   "total_shares_outstanding", "bid", "ask", "premarket_change", "premarket_high",
                                   "relative_volume_10d_calc", "relative_volume_intraday|5", "update_mode", "country"],
                       "range": [0, 50]}).encode()
    js = json.loads(get("https://scanner.tradingview.com/america/scan", body, {"Content-Type": "application/json"}))
    cols = ["name", "exchange", "close", "change", "volume", "float", "acciones", "bid", "ask", "pm_chg", "pm_high",
            "rvol10d", "rvol_intra", "update_mode", "country"]
    for row in js.get("data", []):
        d = dict(zip(cols, row["d"])); out["tv"].setdefault(d["name"], d)
except Exception as e:
    out["errores"].append(f"tradingview: {type(e).__name__}: {e}")
# Finviz y Nasdaq
for tk in SCANNER:
    try:
        h = get(f"https://finviz.com/quote.ashx?t={tk}&p=d")
        def campo(n):
            m = re.search(re.escape(n) + r'</td>\s*<td[^>]*>(?:<[^>]+>)*([^<]+)', h)
            return m.group(1).strip() if m else None
        out["finviz"][tk] = {"float": campo("Shs Float"), "acciones": campo("Shs Outstand"), "short": campo("Short Float")}
    except Exception as e:
        out["finviz"][tk] = {"error": f"{type(e).__name__}: {e}"[:80]}
    try:
        js = json.loads(get(f"https://api.nasdaq.com/api/quote/{tk}/info?assetclass=stocks",
                            extra={"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}))
        p = (js.get("data") or {}).get("primaryData") or {}
        out["nasdaq"][tk] = {k: p.get(k) for k in ("lastSalePrice", "percentageChange", "volume", "bidPrice", "askPrice",
                                                   "lastTradeTimestamp", "isRealTime")}
    except Exception as e:
        out["nasdaq"][tk] = {"error": f"{type(e).__name__}: {e}"[:80]}
    try:
        i = yf.Ticker(tk).info or {}
        out["yahoo"][tk] = {"float": i.get("floatShares"), "bid": i.get("bid"), "ask": i.get("ask"),
                            "bidSize": i.get("bidSize"), "askSize": i.get("askSize"), "precio": i.get("currentPrice")}
    except Exception as e:
        out["yahoo"][tk] = {"error": str(e)[:80]}
    time.sleep(0.5)

def M(x):
    try: return round(float(x) / 1e6, 3)
    except (TypeError, ValueError): return x
def sp(b, a):
    try:
        b, a = float(str(b).replace("$", "")), float(str(a).replace("$", ""))
        return round((a - b) / ((a + b) / 2) * 100, 2) if a > b > 0 else None
    except Exception: return None

print("errores:", out["errores"])
print(f"{'tk':<5} {'scanner':>7} {'yahoo':>7} {'tv':>7} {'finviz':>7} | spread% yahoo / tv / nasdaq | tv update_mode")
for tk, sc in SCANNER.items():
    y, t, f, n = out["yahoo"].get(tk, {}), out["tv"].get(tk, {}), out["finviz"].get(tk, {}), out["nasdaq"].get(tk, {})
    print(f"{tk:<5} {str(sc):>7} {str(M(y.get('float'))):>7} {str(M(t.get('float'))):>7} {str(f.get('float') or f.get('error')):>7} | "
          f"{sp(y.get('bid'), y.get('ask'))} / {sp(t.get('bid'), t.get('ask'))} / {sp(n.get('bidPrice'), n.get('askPrice'))} | "
          f"{t.get('update_mode')}  nasdaq: {n.get('lastSalePrice')} {n.get('lastTradeTimestamp')} {n.get('error','')}")
import pathlib; pathlib.Path("resultados").mkdir(exist_ok=True)
pathlib.Path("resultados/fuentes.json").write_text(json.dumps(out, indent=1, default=str))
