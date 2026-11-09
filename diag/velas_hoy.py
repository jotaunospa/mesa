"""Diagnóstico: velas de 1 min de hoy (regular) para unas acciones, para revisar operaciones de papel."""
import json, sys
import yfinance as yf
tks = sys.argv[1:] or ["STAK", "YMAT"]
df = yf.download(tks, period="1d", interval="1m", prepost=False, group_by="ticker", progress=False, auto_adjust=False)
out = {}
for tk in tks:
    d = df[tk].dropna(subset=["Close"])
    out[tk] = [[i.tz_convert("America/New_York").strftime("%H:%M"), round(x.Open, 4), round(x.High, 4), round(x.Low, 4), round(x.Close, 4), int(x.Volume)] for i, x in d.iterrows()]
json.dump(out, open("velas_hoy.json", "w"))
for tk in tks:
    print(tk, len(out[tk]), out[tk][:1], out[tk][-1:])
