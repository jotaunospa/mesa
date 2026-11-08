"""Aplica los criterios de la revisión semanal (README.md) a la salida de backtest.py.

Uso: python analisis/criterios.py backtest.json veredictos.json [tramos.json DIR_DATOS] > informe.md
Con tramos.json y DIR_DATOS agrega arriba el criterio de detención (detencion.py, sección H).
Los criterios están fijados de antemano; este script no decide nada que no esté en el README.
"""
import json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detencion as D

MIN_OPS, MIN_PROM = 8, 0.3
bt, V = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
g, var = bt["grupos"], bt["variantes"]
clave = lambda x: (x["dia"], x["tk"])
completas = {clave(x) for x in g.get("reglas completas", [])}
DET = D.evaluar(json.load(open(sys.argv[3])), sys.argv[4]) if len(sys.argv) > 4 else None
DETENIDO = bool(DET and DET["estado"] == "se cumple")


def juzgar(ops):
    n = len(ops)
    if n == 0:
        return "sin conclusión", "no bloquea ninguna operación"
    tot = sum(x["R"] for x in ops)
    prom = tot / n
    if n < MIN_OPS:
        return "sin conclusión", f"bloquea {n} (se piden {MIN_OPS} o más); R neto {tot:+.2f}"
    if tot < 0:
        return "protege", f"bloquea {n} con R neto {tot:+.2f} (promedio {prom:+.2f})"
    sin_mejor = tot - max(x["R"] for x in ops)
    por_tk = defaultdict(float)
    for x in ops:
        por_tk[x["tk"]] += x["R"]
    sin_tk = tot - max(por_tk.values())
    if prom >= MIN_PROM and sin_mejor > 0 and sin_tk > 0:
        if DETENIDO:  # sección H: no se proponen ajustes de entrada para la ventana
            return "sin propuesta", f"cumpliría 'sobreprotectora', pero el criterio de detención se cumple: no se ajusta la entrada en esta ventana"
        return "sobreprotectora", f"bloquea {n}, promedio {prom:+.2f} R; sin la mejor {sin_mejor:+.2f}, sin la mejor acción {sin_tk:+.2f}"
    return "se mantiene", f"bloquea {n}, promedio {prom:+.2f} R (no cumple los tres requisitos)"


dias = sorted({r["dia"] for r in V})
print(f"# Revisión semanal de reglas: {dias[0]} a {dias[-1]}\n")
if DETENIDO:
    print("> **Criterio de detención cumplido:** el retorno neto de la ventana de las aptas está bajo cero con 90% de confianza. "
          "Este informe no propone ajustes de entrada para la ventana.\n")
print(f"{len(dias)} días, {len(V)} filas evaluadas con el código real de la Mesa. Papel, costos supuestos; no predice nada.\n")
print("Calibrar con la semana N, validar con la N+1: ningún ajuste se calibra y se valida con los mismos días.\n")
if DET:
    print(D.markdown(DET))
print(f"- Reglas completas: {len(g.get('reglas completas', []))} operaciones, R neto {sum(x['R'] for x in g.get('reglas completas', [])):+.2f}")
print(f"- Sin ningún filtro de la acción: {len(g.get('sin filtros', []))} operaciones, R neto {sum(x['R'] for x in g.get('sin filtros', [])):+.2f}\n")
print("## Reglas de la acción (se quita una a la vez)\n\n| Regla | Veredicto | Detalle |\n|---|---|---|")
REGLAS = ("noticia", "Escáner", "RVOL", "float", "precio", "dilución", "volumen", "pausa", "cambio")
for r in REGLAS:  # mismas reglas que backtest.py; solo cuentan setups válidos
    ops = [x for x in g.get(f"todas menos {r}", []) if clave(x) not in completas]
    v, d = juzgar(ops)
    print(f"| {r} | **{v}** | {d} |")
print("\n## Umbrales del setup (sin los demás filtros)\n\n| Variante | Veredicto | Detalle |\n|---|---|---|")
base = {clave(x) for x in var.get("reglas actuales (stop 2-8%, VWAP +3%)", [])}
for k, L in var.items():
    if k.startswith("reglas actuales"):
        print(f"| {k} | base | {len(L)} operaciones, R neto {sum(x['R'] for x in L):+.2f} |")
        continue
    v, d = juzgar([x for x in L if clave(x) not in base])
    print(f"| {k} | **{v}** | {d} |")
print("\n## Contra la línea base\n")
print("La pregunta es si las reglas eligen mejor que entrar a ciegas en la misma lista a la misma hora.\n")
prom = lambda L: (sum(x["R"] for x in L) / len(L)) if L else None
fmt = lambda L: f"{len(L)} operaciones, promedio {prom(L):+.2f} R" if L else "0 operaciones"
sis = g.get("reglas completas", [])
print(f"- Sistema (reglas completas): {fmt(sis)}")
for k, L in bt.get("base", {}).items():
    print(f"- Línea base, {k}: {fmt(L)}")
az = bt.get("base", {}).get("azar", [])
if len(sis) >= MIN_OPS and az:
    d = prom(sis) - prom(az)
    print(f"\nDiferencia sistema menos azar: {d:+.2f} R por operación. " + ("El sistema elige mejor que el azar esta semana." if d > 0 else "El sistema no supera al azar esta semana."))
else:
    print(f"\nSin conclusión: el sistema necesita {MIN_OPS} operaciones o más para compararlo con el azar.")
print("\n## Operaciones\n")
for k in ["reglas completas"] + sorted(x for x in g if x.startswith("todas menos")):
    for x in g.get(k, []):
        print(f"- {k}: {x['dia']} {x['hora']} {x['tk']} {x['R']:+.2f} R ({x['motivo']})")
for k, L in var.items():
    for x in L:
        print(f"- setup, {k}: {x['dia']} {x['hora']} {x['tk']} {x['R']:+.2f} R ({x['motivo']})")
print("\nUn ajuste marcado \"sobreprotectora\" es provisional: se confirma con una segunda semana antes de cambiar la Mesa.")
