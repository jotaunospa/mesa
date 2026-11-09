"""Diagnóstico: la SEC responde con el contacto que viene del secreto SEC_CONTACTO (no se imprime)."""
import json, os, urllib.request
from lib import sec_mesa
print("contacto configurado:", "@" in sec_mesa.UA)
for url in ["https://data.sec.gov/submissions/CIK0000320193.json", "https://www.sec.gov/files/company_tickers.json"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": sec_mesa.UA}), timeout=20)
        print(url, r.status, len(r.read()))
    except Exception as e:
        print(url, "ERROR", type(e).__name__, str(e)[:120])
