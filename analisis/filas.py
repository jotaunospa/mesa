"""Traduce todas las fotos de los días pedidos al formato de la Mesa (filas.json para veredictos.js).

Uso: python analisis/filas.py DIR_DATOS SALIDA.json [AAAA-MM-DD ...]   (sin días = todos)
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import a_mesa

datos, salida, dias = sys.argv[1], sys.argv[2], sys.argv[3:]
if not dias:
    dias = sorted(os.path.basename(p) for p in glob.glob(f"{datos}/20??-??-??"))
out = []
for dia in dias:
    d = f"{datos}/{dia}"
    v = {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(d + "/velas_vivo/*.json")}
    for f in sorted(glob.glob(d + "/snap-*.json")):
        s = json.load(open(f))
        m = a_mesa.construir(s, v)
        for x, r in zip(m["bench"]["rows"], s["filas"]):
            out.append({"dia": dia, "hora": s["hora_ny"][11:16], "x": x, "apto": r.get("apto"), "motivos": r.get("no_apto_por") or []})
json.dump(out, open(salida, "w"))
print(len(out), "filas de", len(dias), "días")
