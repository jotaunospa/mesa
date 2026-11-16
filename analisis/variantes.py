"""Variantes de ejecución: mediciones sobre los setups que ya detecta la Mesa (no crean reglas).

Dos variantes: medias ordenadas al entrar y último cruce de EMA antes de entrar.
- Población: la misma de excursiones.py. Cada setup que detecta el código real de la Mesa
  (veredictos.json), cumpla o no los umbrales, en la ventana 09:35–10:15, una operación por
  acción y día, con la simulación de backtest.py (entrada si una vela de los 10 min siguientes
  toca el precio de entrada; salida en el stop, en el objetivo 2R o a las 10:30).
- Etiqueta "ordenado": EMA 9 > EMA 20 > VWAP al momento de la entrada. Se usan los valores al
  cierre de la vela ANTERIOR a la vela de entrada (lo que se sabía al entrar, sin mirar el futuro).
  EMA con lib/ema.py: velas de 1 min desde el premarket, primera EMA = promedio simple de n cierres.
  VWAP: acumulado de la sesión regular desde las 09:30 (el mismo del gráfico).
  "no ordenado": cualquier otro orden. "sin dato": menos de 20 velas antes de la entrada (no hay EMA 20).
- Etiqueta "último cruce" (desde 16-11): el último cruce de la EMA 9 con la EMA 20 antes de la vela
  de entrada (mismas velas y mismo corte que arriba): "▲ sobre" (la 9 pasó sobre la 20), "▼ bajo"
  (pasó bajo) o "sin cruce" (no hubo cruce desde que existe la EMA 20 ese día). Se informa también
  cuántos minutos antes de entrar fue. Es la convención "cruce = compra / venta", medida, no aplicada.
- Métricas por grupo: n, acierto (R > 0), R medio y factor de ganancia (ganancias / pérdidas),
  brutos y netos de costos (costos.py: spread de Nasdaq de la foto + deslizamiento escalonado +
  comisión de Racional SUPUESTA). Sin spread en la foto, esa operación no entra al neto.
- Con n < 30 en un grupo: "primeras lecturas, no concluye".
- Cada variante suma 1 al contador de hipótesis del mes (analisis/hipotesis.json).

Papel: no predice nada y no cambia el dictamen, los vetos ni el semáforo.

Uso: python analisis/variantes.py veredictos.json DIR_DATOS SALIDA.json SALIDA.md
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))
import backtest as B
import costos as C
import excursiones as X
from lib.ema import ema

MIN_N = 30
GRUPOS = ("ordenado", "no ordenado", "sin dato")
GRUPOS_CX = ("▲ sobre", "▼ bajo", "sin cruce", "sin dato")


def ultimo_cruce(v, entra: str):
    """Último cruce EMA 9 / EMA 20 con las velas anteriores a la de entrada. (etiqueta, hora del cruce)."""
    i0 = next((i for i, c in enumerate(v) if c[0] == entra), None)
    if not i0:
        return "sin dato", None
    antes = v[:i0]
    cl = [c[4] for c in antes]
    e9, e20 = ema(cl, 9), ema(cl, 20)
    if e20[-1] is None:
        return "sin dato", None
    for i in range(len(antes) - 1, 0, -1):
        if None in (e9[i], e20[i], e9[i - 1], e20[i - 1]):
            break
        a, b = e9[i - 1] - e20[i - 1], e9[i] - e20[i]
        if a <= 0 < b:
            return "▲ sobre", antes[i][0]
        if a >= 0 > b:
            return "▼ bajo", antes[i][0]
    return "sin cruce", None


def minutos(h1: str, h2: str) -> int:
    return (int(h2[:2]) * 60 + int(h2[3:])) - (int(h1[:2]) * 60 + int(h1[3:]))


def estado_medias(v, entra: str):
    """Etiqueta y valores (EMA 9, EMA 20, VWAP) al cierre de la vela anterior a la de entrada."""
    i0 = next((i for i, c in enumerate(v) if c[0] == entra), None)
    if i0 is None or i0 == 0:
        return "sin dato", None
    antes = v[:i0]
    e9, e20 = ema([c[4] for c in antes], 9)[-1], ema([c[4] for c in antes], 20)[-1]
    pv = vv = 0.0
    for c in antes:
        if "09:30" <= c[0] < "16:00":
            pv += (c[2] + c[3] + c[4]) / 3 * c[5]
            vv += c[5]
    vw = pv / vv if vv else None
    if e9 is None or e20 is None or vw is None:
        return "sin dato", {"e9": e9, "e20": e20, "vwap": vw, "velas_antes": len(antes)}
    val = {"e9": round(e9, 4), "e20": round(e20, 4), "vwap": round(vw, 4), "velas_antes": len(antes)}
    return ("ordenado" if e9 > e20 > vw else "no ordenado"), val


def metricas(rs: list[float]) -> dict:
    n = len(rs)
    if not n:
        return {"n": 0}
    gan = sum(r for r in rs if r > 0)
    per = -sum(r for r in rs if r < 0)
    return {"n": n, "acierto_pct": round(sum(r > 0 for r in rs) / n * 100, 1), "R_medio": round(sum(rs) / n, 2),
            "factor": None if per == 0 else round(gan / per, 2), "sin_perdidas": per == 0 and gan > 0}


def contador(mes: str) -> int:
    try:
        H = json.loads((AQUI / "hipotesis.json").read_text())["hipotesis"]
        return sum(1 for h in H if h["mes"] == mes)
    except (OSError, KeyError, ValueError):
        return 0


def fmt(m: dict) -> str:
    if not m.get("n"):
        return "0 | — | — | —"
    fac = "sin pérdidas" if m["sin_perdidas"] else ("—" if m["factor"] is None else f"{m['factor']:.2f}")
    return f"{m['n']} | {m['acierto_pct']:.0f}% | {m['R_medio']:+.2f} | {fac}"


def main(verd, datos, sal_json, sal_md):
    datos = Path(datos)
    V = json.load(open(verd))
    ops, vistos = [], set()
    for r in V:
        s = r.get("setup")
        if not s or not (s.get("live") and s.get("broken", True)) or not (B.ENTRY_START <= r["hora"] < B.ENTRY_END):
            continue
        if (r["dia"], r["tk"]) in vistos:
            continue
        v = B.velas(datos, r["dia"], r["tk"])
        if not v:
            continue
        o = X.recorrer(v, r["hora"], s)
        if o is None:
            continue
        vistos.add((r["dia"], r["tk"]))
        grupo, val = estado_medias(v, o["entra"])
        cx, hcx = ultimo_cruce(v, o["entra"])
        sp = X.spread(datos, r["dia"], r["hora"], r["tk"])
        cost = C.ida_y_vuelta(o["e"], o["x"], sp)
        R = o["e0"] - o["stop"]
        ops.append({"dia": r["dia"], "hora": r["hora"], "tk": r["tk"], "entra": o["entra"], "motivo": o["motivo"],
                    "grupo": grupo, "medias": val, "cruce": cx,
                    "cruce_min_antes": None if hcx is None else minutos(hcx, o["entra"]), "cumple_reglas": bool(s.get("ok")), "R_bruto": o["R_bruto"],
                    "R_neto": None if cost is None else round(o["R_bruto"] - cost / 100 * o["e"] / R, 2)})
    res = {}
    for g in GRUPOS:
        L = [x for x in ops if x["grupo"] == g]
        res[g] = {"bruto": metricas([x["R_bruto"] for x in L]),
                  "neto": metricas([x["R_neto"] for x in L if x["R_neto"] is not None])}
    res_cx = {}
    for g in GRUPOS_CX:
        L = [x for x in ops if x["cruce"] == g]
        mins = sorted(x["cruce_min_antes"] for x in L if x["cruce_min_antes"] is not None)
        res_cx[g] = {"bruto": metricas([x["R_bruto"] for x in L]),
                     "neto": metricas([x["R_neto"] for x in L if x["R_neto"] is not None]),
                     "min_antes_mediana": mins[len(mins) // 2] if mins else None}
    dias = sorted({x["dia"] for x in ops})
    mes = (dias[-1] if dias else "")[:7]
    nh = contador(mes) if mes else 0
    json.dump({"variante": "ema-ordenada", "ops": ops, "resumen": res, "resumen_cruce": res_cx, "hipotesis_mes": nh}, open(sal_json, "w"),
              ensure_ascii=False, indent=1)

    chica = any(0 < res[g]["bruto"]["n"] < MIN_N for g in ("ordenado", "no ordenado"))
    md = ["## Variantes de ejecución (solo medición, no crean reglas)", "",
          f"Hipótesis probadas en {mes or 'el mes'}: **{nh}** (contador en `analisis/hipotesis.json`). "
          "Con muchas pruebas, alguna sale bien por azar: un resultado aislado no es un hallazgo.", "",
          "### Medias ordenadas al entrar (EMA 9 > EMA 20 > VWAP)", "",
          f"{len(ops)} setups activados en {len(dias)} días ({', '.join(dias) or '—'}), una operación por acción y día, "
          "cumplan o no los umbrales. Medias al cierre de la vela anterior a la entrada; EMA con las velas desde el premarket.", ""]
    if chica or not ops:
        md += ["**Primeras lecturas, no concluye** (n < 30 en algún grupo).", ""]
    md += ["| Grupo | n bruto | acierto | R medio | factor de ganancia | n neto | acierto | R medio | factor de ganancia |",
           "|---|---|---|---|---|---|---|---|---|"]
    for g in GRUPOS:
        md.append(f"| {g} | {fmt(res[g]['bruto'])} | {fmt(res[g]['neto'])} |")
    md += ["", C.nota(), "",
           "\"sin dato\": menos de 20 velas de 1 min antes de la entrada, no hay EMA 20. "
           "El neto deja fuera las operaciones sin spread en la foto.", ""]
    chica_cx = any(0 < res_cx[g]["bruto"]["n"] < MIN_N for g in ("▲ sobre", "▼ bajo", "sin cruce"))
    md += ["### Último cruce de EMA antes de entrar", "",
           "La convención de los cursos dice \"▲ = compra, ▼ = venta\". Aquí solo se mide: los mismos setups, "
           "separados por el último cruce de la EMA 9 con la EMA 20 antes de la vela de entrada.", ""]
    if chica_cx or not ops:
        md += ["**Primeras lecturas, no concluye** (n < 30 en algún grupo).", ""]
    md += ["| Último cruce | n bruto | acierto | R medio | factor de ganancia | n neto | acierto | R medio | factor de ganancia | minutos antes (mediana) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for g in GRUPOS_CX:
        mm = res_cx[g]["min_antes_mediana"]
        md.append(f"| {g} | {fmt(res_cx[g]['bruto'])} | {fmt(res_cx[g]['neto'])} | {'—' if mm is None else mm} |")
    md += ["", "\"sin cruce\": no hubo cruce desde que existe la EMA 20 ese día. \"sin dato\": menos de 20 velas antes de entrar.", ""]
    cnt = Counter(x["grupo"] for x in ops)
    if ops:
        md.append("<details><summary>Operaciones (" + ", ".join(f"{g}: {cnt[g]}" for g in GRUPOS) + ")</summary>\n")
        for x in sorted(ops, key=lambda z: (z["dia"], z["entra"])):
            m = x["medias"] or {}
            md.append(f"- {x['dia']} {x['entra']} {x['tk']}: {x['grupo']}"
                      + (f" (EMA 9 {m['e9']}, EMA 20 {m['e20']}, VWAP {m['vwap']})" if x["grupo"] != "sin dato" else
                         f" ({m.get('velas_antes', 0)} velas antes)")
                      + f", {x['R_bruto']:+.2f} R bruto, " + ("neto sin dato" if x["R_neto"] is None else f"{x['R_neto']:+.2f} R neto")
                      + f" ({x['motivo']})" + (f"; último cruce {x['cruce']} {x['cruce_min_antes']} min antes" if x["cruce_min_antes"] is not None else f"; {x['cruce']}"))
        md.append("\n</details>")
    Path(sal_md).write_text("\n".join(md) + "\n")
    print("\n".join(md[:20]))


if __name__ == "__main__":
    main(*sys.argv[1:5])
