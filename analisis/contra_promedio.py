"""Cada operación medida contra la candidata promedio de ese mismo día y tramo (idea de TradingAgents, sin LLM).

Pregunta: ¿la operación le ganó a comprar cualquier candidata a la misma hora y venderla a la misma hora?
Si comprar cualquier candidata ya pierde, un GO solo aporta si pierde menos o gana.

- Operaciones: las de excursiones.json (todos los setups activados, una por acción y día), separadas en
  "cumplen los umbrales del setup" y "no cumplen". Los GO con reglas completas son un subgrupo de los
  primeros; mientras sean 0, se miden los setups.
- Candidata promedio: todas las candidatas válidas (sin "dato rancio") que el Escáner había mostrado ese día
  hasta la hora de entrada, compradas a la apertura de la vela de entrada y vendidas al cierre de la vela de
  salida de la operación. Mismo tramo, mismas velas (velas/TICKER.json.gz).
- Diferencia = retorno % de la operación − retorno % promedio de esas candidatas. Bruta y neta (neta: cada
  una con sus costos de costos.py; sin spread, esa candidata o esa operación queda fuera del neto).
- Se une a la línea base al azar de backtest.py (sección "Contra la línea base"): aquella compara en R
  contra entradas al azar; esta compara en % contra el promedio exacto del mismo tramo.
Uso: python analisis/contra_promedio.py excursiones.json DIR_DATOS SALIDA.json SALIDA.md
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))
import backtest as B
import costos as C
from lib import rancio

MIN_N = 30


def vistas_hasta(datos: Path, dia: str, hora: str) -> dict:
    """{ticker: spread_pct} de candidatas válidas vistas en fotos hasta esa hora."""
    out = {}
    for p in sorted((datos / dia).glob("snap-*.json")):
        hh = f"{p.stem[5:7]}:{p.stem[7:]}"
        if hh > hora:
            break
        for f in rancio.separar(json.loads(p.read_text()).get("filas", []))[0]:
            out.setdefault(f["ticker"], f.get("spread_pct"))
    return out


def tramo(v, entra: str, sale: str):
    a = next((c for c in v if c[0] >= entra), None)
    b = [c for c in v if c[0] <= sale]
    if not a or not b or not a[1]:
        return None
    return a[1], b[-1][4]


def main(exc, datos, sal_json, sal_md):
    datos = Path(datos)
    ops = json.load(open(exc))["operaciones"]
    res = []
    for o in ops:
        pe, px = o["e"], o["x"]
        r_op = (px / pe - 1) * 100
        cost = C.ida_y_vuelta(pe, px, o.get("spread_pct"))
        brutos, netos = [], []
        for tk, sp in vistas_hasta(datos, o["dia"], o["entra"]).items():
            v = B.velas(datos, o["dia"], tk)
            t = tramo(v or [], o["entra"], o["sale"])
            if not t:
                continue
            r = (t[1] / t[0] - 1) * 100
            brutos.append(r)
            c = C.ida_y_vuelta(t[0], t[1], sp)
            if c is not None:
                netos.append(r - c)
        if not brutos:
            continue
        res.append({"dia": o["dia"], "tk": o["tk"], "entra": o["entra"], "sale": o["sale"], "cumple_reglas": o["cumple_reglas"],
                    "op_bruto": round(r_op, 2), "op_neto": None if cost is None else round(r_op - cost, 2),
                    "prom_bruto": round(st.mean(brutos), 2), "n_cand": len(brutos),
                    "prom_neto": round(st.mean(netos), 2) if netos else None, "n_cand_neto": len(netos)})
    json.dump(res, open(sal_json, "w"), ensure_ascii=False, indent=1)

    def fila(nombre, L):
        db = [x["op_bruto"] - x["prom_bruto"] for x in L]
        dn = [x["op_neto"] - x["prom_neto"] for x in L if x["op_neto"] is not None and x["prom_neto"] is not None]
        if not db:
            return f"| {nombre} | 0 | — | — | — | — |"
        f = lambda a: f"{st.mean(a):+.2f} / {st.median(a):+.2f}"
        return (f"| {nombre} | {len(db)} | {f(db)} | {sum(d > 0 for d in db)} de {len(db)} | "
                f"{len(dn)} | {f(dn) if dn else '—'} |")

    go = [x for x in res if x["cumple_reglas"]]
    md = ["### Contra la candidata promedio del mismo día y tramo", "",
          "Cada operación contra comprar todas las candidatas válidas que el Escáner ya había mostrado ese día, a la misma hora "
          "de entrada y de salida. Diferencia en puntos porcentuales (operación − promedio). Complementa la línea base al azar de arriba (en R). "
          "Los GO con reglas completas son los de \"Reglas completas\" en el criterio de detención; mientras sean 0, se miden los setups.", ""]
    if len(res) < MIN_N or len(go) < MIN_N:
        md += ["**Primeras lecturas, no concluye** (n < 30).", ""]
    md += ["| Operaciones | n | diferencia bruta (prom. / mediana) | le ganan al promedio | n neto | diferencia neta (prom. / mediana) |",
           "|---|---|---|---|---|---|",
           fila("cumplen los umbrales del setup", go), fila("no cumplen los umbrales", [x for x in res if not x["cumple_reglas"]]),
           fila("todos los setups", res), "", C.nota()]
    Path(sal_md).write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main(*sys.argv[1:5])
