"""Diagnóstico de float: Yahoo (todas sus variantes) vs SEC vs scanner (05-11 13:43 ET)."""
import json, sys
sys.path.insert(0, ".")
import yfinance as yf
from lib.sec_mesa import cliente

SCANNER = {"LABT": 0.848, "AEHL": 0.677, "NTCL": 1.70, "ZNB": 4.55, "AMTX": 67.16, "FLYE": 1.56,
           "PLCE": 8.09, "BDMD": 6.50, "MASK": 0.646, "JDZG": 4.85}
c = cliente()
out = []
for tk, sc in SCANNER.items():
    i = yf.Ticker(tk).info or {}
    fila = {"ticker": tk, "scanner_M": sc,
            "y_floatShares": i.get("floatShares"), "y_sharesOutstanding": i.get("sharesOutstanding"),
            "y_impliedSharesOutstanding": i.get("impliedSharesOutstanding"),
            "y_heldInsiders": i.get("heldPercentInsiders"), "y_heldInst": i.get("heldPercentInstitutions"),
            "pais": i.get("country"), "y_lastSplit": [i.get("lastSplitFactor"), i.get("lastSplitDate")]}
    try:
        sp = yf.Ticker(tk).splits
        fila["y_splits"] = [[str(d.date()), float(v)] for d, v in sp.tail(3).items()] if sp is not None else []
    except Exception as e:
        fila["y_splits"] = str(e)
    cik = c.cik(tk)
    fila["cik"] = cik
    if cik:
        st, f, _ = c._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json", "data.sec.gov")
        if st == 200 and f:
            ent = (((f.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares", [])
            ent = sorted(ent, key=lambda e: (e.get("end", ""), e.get("filed", "")))
            fila["sec_portadas"] = [{"fecha": e["end"], "form": e.get("form"), "filed": e.get("filed"), "acciones": e["val"]} for e in ent[-4:]]
            pf = (((f.get("facts") or {}).get("dei") or {}).get("EntityPublicFloat") or {}).get("units", {}).get("USD", [])
            fila["sec_public_float_usd"] = pf[-1] if pf else None
        st, s, _ = c._get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", "data.sec.gov")
        if st == 200 and s:
            rec = s["filings"]["recent"]
            fila["sec_ultimos"] = [[a, b] for a, b in list(zip(rec["form"], rec["filingDate"]))[:12]]
    out.append(fila)

def m(x): return round(x / 1e6, 3) if x else None
print(f"{'tk':<5} {'scanner':>8} {'y_float':>8} {'y_out':>8} {'y_impl':>8} {'sec_portada (fecha,form)':>32}  pais")
for f in out:
    p = (f.get("sec_portadas") or [{}])[-1]
    print(f"{f['ticker']:<5} {f['scanner_M']:>8} {str(m(f['y_floatShares'])):>8} {str(m(f['y_sharesOutstanding'])):>8} "
          f"{str(m(f['y_impliedSharesOutstanding'])):>8} {str(m(p.get('acciones')))+' '+str(p.get('fecha'))+' '+str(p.get('form')):>32}  {f['pais']}  splits {f['y_splits']}")
import pathlib; pathlib.Path("resultados").mkdir(exist_ok=True)
pathlib.Path("resultados/float_check.json").write_text(json.dumps(out, indent=1, default=str))
