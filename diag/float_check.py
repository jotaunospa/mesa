"""Diagnóstico 06-11: dilución de 7 factores, short interest y RVOL por hora contra el scanner."""
import json, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, ".")
import yfinance as yf
import pandas as pd
from lib import sec_mesa

NY = ZoneInfo("America/New_York")
# Dilución que mostró el scanner (05-11 y 06-11)
DIL = {"LABT": "alta", "AEHL": "media", "ZNB": "alta", "BDMD": "alta", "PLCE": "media", "FLYE": "alta",
       "JDZG": "media", "STAK": "media", "DNUT": "baja", "AMTX": "baja", "VIVK": "alta", "CIIT": "media",
       "RAYA": "media", "CDT": "alta", "SDA": "baja", "DBGI": "alta", "VSA": "alta", "SNT": "baja",
       "HPAI": "media", "FLGT": "baja", "ONCO": "media", "SKIN": "baja", "PSQH": "alta"}
# Short interest del scanner (SH INT, %)
SHORT = {"LABT": 11, "AEHL": 34, "ZNB": 4.1, "BDMD": 0.3, "PLCE": 40, "FLYE": 18, "JDZG": 14, "STAK": 11,
         "DNUT": 20, "AMTX": 12, "VIVK": 22, "CIIT": 2.1, "RAYA": 0.3, "SDA": 1.5, "DBGI": 13, "VSA": 1.3,
         "SNT": 0.1, "HPAI": 0.3, "FLGT": 13, "ONCO": 0.9, "SKIN": 14, "PSQH": 3.1}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
      "Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
out = {"dil": [], "short": [], "rvol": [], "nasdaq_pre": {}}
lin = []
for tk, sc in DIL.items():
    try:
        t = yf.Ticker(tk); inf = t.info or {}
        sp = t.splits
        s12 = []
        if sp is not None and len(sp):
            sp.index = sp.index.tz_convert(NY)
            s12 = [{"fecha": str(i.date()), "ratio": float(v)} for i, v in sp[sp.index >= pd.Timestamp.now(tz=NY) - pd.Timedelta(days=365)].items()]
        d = sec_mesa.dilucion(tk, splits_12m=s12)
        f7 = d.get("dil7") or {}
        out["dil"].append({"tk": tk, "scanner": sc, "v12": d.get("dil"), **f7, "extranjero": d.get("extranjero")})
        lin.append(f"DIL {tk:<5} scanner={sc:<5} v1.2={d.get('dil')!s:<5} puntaje={f7.get('puntaje')}/{f7.get('cobertura')} "
                   f"{ {k: v for k, v in (f7.get('factores') or {}).items()} } {f7.get('detalle')} ext={d.get('extranjero')}")
        # short
        ss = inf.get("sharesShort"); fy = inf.get("floatShares")
        tvf = None
        out["short"].append({"tk": tk, "scanner": SHORT.get(tk), "sharesShort": ss, "fecha": inf.get("dateShortInterest"),
                             "yahoo_pct": inf.get("shortPercentOfFloat"), "float_yahoo": fy, "acciones": inf.get("sharesOutstanding")})
        lin.append(f"SHORT {tk:<5} scanner={SHORT.get(tk)} yahoo%={round((inf.get('shortPercentOfFloat') or 0)*100,2)} "
                   f"short/floatY={round(ss/fy*100,2) if ss and fy else None} short/acciones={round(ss/inf['sharesOutstanding']*100,2) if ss and inf.get('sharesOutstanding') else None} "
                   f"sharesShort={ss} fecha={inf.get('dateShortInterest')}")
    except Exception as e:
        lin.append(f"ERR {tk}: {type(e).__name__}: {e}"[:200])
    time.sleep(0.3)
# Nasdaq premarket (formato)
for tk in ["CIIT", "VSA", "SDA"]:
    for tipo in ["pre"]:
        try:
            u = f"https://api.nasdaq.com/api/quote/{tk}/extended-trading?markettype={tipo}&assetclass=stocks&time=0"
            js = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15).read())
            out["nasdaq_pre"][tk] = js.get("data")
            lin.append(f"NASDAQ-PRE {tk}: {json.dumps(js.get('data'))[:600]}")
        except Exception as e:
            lin.append(f"NASDAQ-PRE {tk} error {type(e).__name__}: {e}"[:200])
import pathlib; pathlib.Path("resultados").mkdir(exist_ok=True)
pathlib.Path("resultados/diag.json").write_text(json.dumps(out, indent=1, default=str))
print("\n".join(lin))
