"""Diagnóstico: ¿qué fuentes gratis entregan una lista de acciones que se mueven en premarket?"""
import json, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
NQ = dict(UA, Origin="https://www.nasdaq.com", Referer="https://www.nasdaq.com/")

def get(url, h=NQ, body=None):
    try:
        req = urllib.request.Request(url, data=body, headers=h)
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return type(e).__name__, str(e)[:200]

for u in ["https://api.nasdaq.com/api/marketmovers?assetclass=stocks&exchangestatus=premarket&limit=20",
          "https://api.nasdaq.com/api/marketmovers?assetclass=stocks&exchangestatus=preMarket",
          "https://api.nasdaq.com/api/quote/list-type/premarket?assetclass=stocks",
          "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&exchange=nasdaq&download=true",
          "https://api.nasdaq.com/api/marketmovers?assetclass=stocks"]:
    s, t = get(u)
    print("\n==", u, s); print(t[:1500])

body = json.dumps({"markets": ["america"], "symbols": {"query": {"types": []}, "tickers": []},
                   "filter": [{"left": "premarket_change", "operation": "greater", "right": 10},
                              {"left": "premarket_volume", "operation": "greater", "right": 30000},
                              {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]}],
                   "columns": ["name", "premarket_close", "premarket_change", "premarket_volume", "premarket_high",
                               "close", "float_shares_outstanding", "update_mode"],
                   "sort": {"sortBy": "premarket_change", "sortOrder": "desc"}, "range": [0, 25]}).encode()
s, t = get("https://scanner.tradingview.com/america/scan", dict(UA, **{"Content-Type": "application/json"}), body)
print("\n== tradingview premarket", s); print(t[:3000])
