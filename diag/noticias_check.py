"""Diagnóstico del feed de noticias: las acciones de la lista del 06-11, en tres fuentes.

Guarda resultados/noticias.json con todo lo que trajo cada fuente y la evaluación a la hora de
su primera foto en la ventana (o de su primera foto del día).
"""
import json, subprocess, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yfinance as yf
from lib import noticias as N

dia = "2026-11-06"
subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", "datos"], check=True)
fotos = {}
lista = subprocess.run(["git", "ls-tree", "--name-only", "FETCH_HEAD", f"{dia}/"], capture_output=True, text=True).stdout.split()
for p in sorted(x for x in lista if "/snap-" in x):
    s = json.loads(subprocess.run(["git", "show", f"FETCH_HEAD:{p}"], capture_output=True, text=True).stdout)
    for r in s["filas"]:
        fotos.setdefault(r["ticker"], (s["hora_ny"], r.get("noticias") or [], (r.get("meta") or {}).get("nombre")))
out = {}
for tk, (hora, yahoo, nombre) in sorted(fotos.items()):
    if not nombre:
        try:
            nombre = yf.Ticker(tk).info.get("longName")
        except Exception:
            nombre = None
    g, q = N.google(tk, nombre), N.nasdaq(tk)
    y = [{**n, "origen": "yahoo"} for n in yahoo]
    ev = N.evaluar(tk, nombre, datetime.fromisoformat(hora), {"google": g, "nasdaq": q, "yahoo": y})
    out[tk] = {"hora": hora, "nombre": nombre, "google": g, "nasdaq": q, "yahoo": y, "eval": ev}
    print(f"{tk:<5} {hora[11:16]} {str(nombre)[:28]:<28} google {'X' if g is None else len(g):>3}  nasdaq {'X' if q is None else len(q):>3}  "
          f"-> {ev['estado']:<13} {(ev['validas'][0]['origen'] + ': ' + ev['validas'][0]['titulo'][:70]) if ev['validas'] else ''}")
Path("resultados").mkdir(exist_ok=True)
Path("resultados/noticias.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
