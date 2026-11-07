"""Aplica los criterios de la revisión semanal (README.md) a la salida de backtest.py.

Uso: python analisis/criterios.py backtest.json veredictos.json > informe.md
Los criterios están fijados de antemano; este script no decide nada que no esté en el README.
"""
import json, sys
from collections import defaultdict

MIN_OPS, MIN_PROM = 8, 0.3
bt, V = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
g, var = bt["grupos"], bt["variantes"]
clave = lambda x: (x["dia"], x["tk"])
completas = {clave(x) for x in g.get("reglas completas", [])}


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
        return "sobreprotectora", f"bloquea {n}, promedio {prom:+.2f} R; sin la mejor {sin_mejor:+.2f}, sin la mejor acción {sin_tk:+.2f}"
    return "se mantiene", f"bloquea {n}, promedio {prom:+.2f} R (no cumple los tres requisitos)"


dias = sorted({r["dia"] for r in V})
print(f"# Revisión semanal de reglas: {dias[0]} a {dias[-1]}\n")
print(f"{len(dias)} días, {len(V)} filas evaluadas con el código real de la Mesa. Papel, costos supuestos; no predice nada.\n")
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
print("\n## Operaciones\n")
for k in ["reglas completas"] + sorted(x for x in g if x.startswith("todas menos")):
    for x in g.get(k, []):
        print(f"- {k}: {x['dia']} {x['hora']} {x['tk']} {x['R']:+.2f} R ({x['motivo']})")
for k, L in var.items():
    for x in L:
        print(f"- setup, {k}: {x['dia']} {x['hora']} {x['tk']} {x['R']:+.2f} R ({x['motivo']})")
print("\nUn ajuste marcado \"sobreprotectora\" es provisional: se confirma con una segunda semana antes de cambiar la Mesa.")
