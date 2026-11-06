"""Diagnóstico 06-11: RVOL por hora con distintas bases contra el TOD RVOL del scanner."""
import json, sys
sys.path.insert(0, ".")
import yfinance as yf, pandas as pd
from zoneinfo import ZoneInfo
NY = ZoneInfo("America/New_York")
# TOD RVOL del scanner: (hora NY, valor)
OBJ = {"VIVK": [("09:44", 82)], "CIIT": [("09:44", 3220), ("10:30", 3581)], "RAYA": [("09:44", 1338), ("10:30", 5341)],
       "CDT": [("09:44", 0.5), ("10:30", 0.7)], "SDA": [("09:44", 98), ("10:30", 3687)], "DBGI": [("09:44", 1404)],
       "VSA": [("09:44", 2250), ("10:30", 2373)], "SNT": [("09:44", 66)], "HPAI": [("09:44", 0.3), ("10:30", 0.4)],
       "FLGT": [("09:44", 2.9), ("10:30", 3.6)], "SKIN": [("10:30", 6.5)], "PSQH": [("10:30", 67)]}
lin = []
for tk, objs in OBJ.items():
    t = yf.Ticker(tk)
    h = t.history(period="60d", interval="5m", prepost=True)
    if h is None or h.empty:
        lin.append(f"{tk}: sin historia 5m"); continue
    h.index = h.index.tz_convert(NY)
    h = h.assign(d=h.index.date, m=h.index.hour * 60 + h.index.minute)
    hoy = h["d"].max()
    dias = sorted(x for x in h["d"].unique() if x < hoy)
    avgd = (t.info or {}).get("averageVolume10days") or (t.info or {}).get("averageDailyVolume10Day")
    for hhmm, obj in objs:
        mm = int(hhmm[:2]) * 60 + int(hhmm[3:])
        hoy_df = h[h["d"] == hoy]
        num_all = hoy_df[hoy_df["m"] < mm]["Volume"].sum()
        num_reg = hoy_df[(hoy_df["m"] >= 570) & (hoy_df["m"] < mm)]["Volume"].sum()
        res = {}
        for N in (5, 10, 20, 30, 50):
            ds = dias[-N:]
            prev = h[h["d"].isin(ds)]
            base_all = prev[prev["m"] < mm].groupby("d")["Volume"].sum().reindex(ds, fill_value=0).mean()
            base_reg = prev[(prev["m"] >= 570) & (prev["m"] < mm)].groupby("d")["Volume"].sum().reindex(ds, fill_value=0).mean()
            res[f"N{N}_all"] = round(num_all / base_all, 1) if base_all else None
            res[f"N{N}_reg"] = round(num_reg / base_reg, 1) if base_reg else None
        lin.append(f"{tk} {hhmm} scanner={obj}  num_all={int(num_all)} num_reg={int(num_reg)} dias={len(dias)} avgVol10d={avgd}  {res}")
print("\n".join(lin))
import pathlib; pathlib.Path("resultados").mkdir(exist_ok=True); pathlib.Path("resultados/diag.json").write_text("{}")
