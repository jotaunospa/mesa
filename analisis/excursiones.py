"""Cuánto se va en contra y a favor cada setup de la Mesa (solo medición; no cambia reglas).

Toma cada setup que detecta el código real de la Mesa (veredictos.json), con la misma
simulación que analisis/backtest.py: se activa si alguna vela de los 10 min siguientes a la
foto toca la entrada (se llena en la entrada o en la apertura si ya la pasó; no se persigue si
ya pasó el objetivo), sale en el stop, en el objetivo 2R o a las 10:30. Una operación por
acción y día. Se miden TODOS los setups detectados (cumplan o no los umbrales de stop y VWAP),
para ver dónde caen el MAE y el MFE.

- MAE (excursión adversa máxima): lo más bajo que llegó desde la entrada hasta la salida, en %
  de la entrada y en R. En la vela del stop cuenta el stop (lo de más abajo ya es después de salir).
- MFE (excursión favorable máxima): lo más alto que llegó hasta la salida (en la vela del stop no cuenta).
- Velocidad: tramos de velas de 1 min seguidas en la misma dirección (cierre contra cierre), en %
  por minuto, subidas contra bajadas, de 09:30 a 16:00.
- Deslizamiento observado: al entrar, apertura sobre la entrada planeada; al salir con stop, la
  vela del stop abriendo bajo el stop (salto). El salto es un mínimo: dentro de la vela no se ve.

R bruto y neto (spread de Nasdaq de la foto + deslizamiento escalonado + comisión supuesta; ver costos.py).

Uso: python analisis/excursiones.py veredictos.json DIR_DATOS SALIDA.json SALIDA.md
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B
import costos as C

MIN_N = 30
SALIDA = B.EXIT


def mas(h, m):
    t = int(h[:2]) * 60 + int(h[3:]) + m
    return f"{t // 60:02d}:{t % 60:02d}"


def spread(datos: Path, dia: str, hora: str, tk: str):
    p = datos / dia / f"snap-{hora.replace(':', '')}.json"
    if not p.exists():
        return None
    for f in json.loads(p.read_text()).get("filas", []):
        if f["ticker"] == tk:
            return f.get("spread_pct")
    return None


def recorrer(v, hora, s):
    """Simulación de backtest.simular, guardando el camino. None si no se activa."""
    e0, sl, tg = s["entry"], s["stop"], s["tgt"]
    lim = mas(hora, 10)
    i0 = None
    for i, c in enumerate(v):
        if c[0] < hora:
            continue
        if c[0] > lim or c[0] >= B.ENTRY_END:
            return None
        if c[2] >= e0:
            i0 = i
            break
    if i0 is None:
        return None
    c = v[i0]
    e = max(e0, c[1])
    if e >= tg:
        return None
    lo, hi = e, e
    salida = None
    for j in range(i0, len(v)):
        c = v[j]
        if j > i0 and c[0] >= SALIDA:
            salida = (c[1], "hora", j, None); break
        if c[3] <= sl:
            salida = (min(sl, c[1]) if j > i0 else sl, "stop", j, sl); lo = min(lo, sl); break
        hi = max(hi, c[2])
        lo = min(lo, c[3])
        if j > i0 and c[2] >= tg:
            salida = (tg, "objetivo", j, None); break
    if salida is None:
        salida = (v[-1][4], "fin de datos", len(v) - 1, None)
    x, motivo, j, stop_plan = salida
    R = e0 - sl
    return {"e0": e0, "e": e, "stop": sl, "tgt": tg, "x": x, "motivo": motivo, "entra": v[i0][0], "sale": v[j][0],
            "x_stop_plan": stop_plan, "mae_pct": round((e - lo) / e * 100, 2), "mfe_pct": round((hi - e) / e * 100, 2),
            "mae_R": round((e - lo) / R, 2), "mfe_R": round((hi - e) / R, 2), "R_bruto": round((x - e) / R, 2),
            "desliz_entrada_pct": round((e - e0) / e0 * 100, 2),
            "salto_stop_pct": round((sl - x) / sl * 100, 2) if motivo == "stop" else None,
            "rango_vela_stop_pct": round((v[j][2] - v[j][3]) / sl * 100, 2) if motivo == "stop" else None}


def piernas(v, desde="09:30", hasta="16:00"):
    """Tramos seguidos en la misma dirección: (dirección, % absoluto, minutos)."""
    cs = [c[4] for c in v if desde <= c[0] < hasta and c[4]]
    out, i = [], 1
    while i < len(cs):
        d = (cs[i] > cs[i - 1]) - (cs[i] < cs[i - 1])
        if d == 0:
            i += 1; continue
        j = i
        while j + 1 < len(cs) and ((cs[j + 1] > cs[j]) - (cs[j + 1] < cs[j])) in (d, 0):
            j += 1
        a, b = cs[i - 1], cs[j]
        out.append(("sube" if d > 0 else "baja", abs(b / a - 1) * 100, j - i + 1))
        i = j + 1
    return out


def q(vals, p):
    vals = sorted(vals)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    f = int(k)
    return vals[f] + (vals[min(f + 1, len(vals) - 1)] - vals[f]) * (k - f)


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
        o = recorrer(v, r["hora"], s)
        if o is None:
            continue
        vistos.add((r["dia"], r["tk"]))
        sp = spread(datos, r["dia"], r["hora"], r["tk"])
        cost = C.ida_y_vuelta(o["e"], o["x"], sp)
        R = o["e0"] - o["stop"]
        o.update({"dia": r["dia"], "hora": r["hora"], "tk": r["tk"], "setup": s["name"], "cumple_reglas": bool(s.get("ok")),
                  "stop_pct": s["pct"], "spread_pct": sp,
                  "R_neto": None if cost is None else round(o["R_bruto"] - cost / 100 * o["e"] / R, 2)})
        ops.append(o)
    # Velocidad: en las acciones con setup y en todas las candidatas (más casos).
    vel = {"con setup": [], "todas las candidatas (09:30–10:30)": []}
    for (d, tk) in vistos:
        vel["con setup"] += piernas(B.velas(datos, d, tk))
    for p in sorted(datos.glob("20??-??-??/resultados.json")):
        for c in json.loads(p.read_text())["candidatas"]:
            v = B.velas(datos, p.parent.name, c["ticker"])
            if v:
                vel["todas las candidatas (09:30–10:30)"] += piernas(v, "09:30", "10:30")
    json.dump({"operaciones": ops}, open(sal_json, "w"), ensure_ascii=False)
    Path(sal_md).write_text(markdown(ops, vel))
    print(f"excursiones: {len(ops)} setups activados")


def markdown(ops, vel) -> str:
    aviso = lambda n: " **Primeras lecturas, no concluye.**" if n < MIN_N else ""
    md = ["## Excursiones de los setups: MAE, MFE, velocidad y deslizamiento\n",
          f"{len(ops)} setups activados (todos los que detectó la Mesa en la ventana, cumplan o no sus umbrales; uno por acción y día). "
          "Simulación de backtest.py: entrada en el precio del setup o en la apertura si ya lo pasó, stop, objetivo 2R, cierre 10:30.\n",
          C.nota() + "\n"]
    if not ops:
        md.append("Sin setups activados en el período: no hay MAE ni MFE que medir.")
    else:
        md.append("| Día | Hora | Acción | Cumple reglas | Stop pedido | Entra | Sale | MAE % (R) | MFE % (R) | R bruto | R neto |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for o in ops:
            md.append(f"| {o['dia']} | {o['hora']} | {o['tk']} | {'sí' if o['cumple_reglas'] else 'no'} | {o['stop_pct']}% | {o['entra']} | "
                      f"{o['sale']} ({o['motivo']}) | {o['mae_pct']} ({o['mae_R']}) | {o['mfe_pct']} ({o['mfe_R']}) | {o['R_bruto']:+.2f} | " +
                      ("sin dato" if o["R_neto"] is None else format(o["R_neto"], "+.2f")) + " |")
        bien = [o for o in ops if o["R_neto"] is not None and o["R_neto"] > 0]
        mal = [o for o in ops if not (o["R_neto"] is not None and o["R_neto"] > 0)]
        md.append(f"\n**b) Setups que terminaron bien (R neto > 0): n = {len(bien)} de {len(ops)}.**{aviso(len(bien))}")
        for nom, L in (("terminaron bien", bien), ("el resto", mal)):
            if not L:
                md.append(f"- {nom}: n = 0.")
                continue
            mae, mfe = [o["mae_pct"] for o in L], [o["mfe_pct"] for o in L]
            md.append(f"- {nom} (n = {len(L)}): MAE mediana {st.median(mae):.2f}% (p75 {q(mae, .75):.2f}%, p90 {q(mae, .9):.2f}%); "
                      f"MFE mediana {st.median(mfe):.2f}% (p75 {q(mfe, .75):.2f}%). "
                      f"MAE bajo 2%: {sum(m < 2 for m in mae)}; entre 2 y 8%: {sum(2 <= m <= 8 for m in mae)}; sobre 8%: {sum(m > 8 for m in mae)}.")
        md.append("La regla pide un stop de 2 a 8% de la entrada. Si las ganadoras tienen el MAE típico dentro de ese rango, la regla "
                  "no las corta; si está sobre 8%, un stop de 8% las habría sacado antes de ganar.")
        de = [o["desliz_entrada_pct"] for o in ops]
        ss = [o["salto_stop_pct"] for o in ops if o["salto_stop_pct"] is not None]
        rv = [o["rango_vela_stop_pct"] for o in ops if o["rango_vela_stop_pct"] is not None]
        md.append(f"\n**Deslizamiento observado:** al entrar (apertura sobre la entrada planeada), n = {len(de)}: promedio {st.mean(de):.2f}%. "
                  + (f"Al salir con stop, n = {len(ss)}: salto bajo el stop promedio {st.mean(ss):.2f}% (mínimo observable); "
                     f"rango de la vela del stop promedio {st.mean(rv):.2f}% (cota alta)." if ss else "Al salir con stop: n = 0.")
                  + aviso(len(ops)))
    md.append("\n**Velocidad de subidas y bajadas** (velas de 1 min seguidas en la misma dirección)\n")
    md.append("| Grupo | n subidas | n bajadas | subida %/min | bajada %/min | bajada ÷ subida | min por subida | min por bajada |")
    md.append("|---|---|---|---|---|---|---|---|")
    for g, L in vel.items():
        up = [p for p in L if p[0] == "sube"]
        dn = [p for p in L if p[0] == "baja"]
        if not up or not dn:
            md.append(f"| {g} | {len(up)} | {len(dn)} | — | — | — | — | — |")
            continue
        su, sd = st.mean(p[1] / p[2] for p in up), st.mean(p[1] / p[2] for p in dn)
        md.append(f"| {g} | {len(up)}{' ⚠' if len(up) < MIN_N else ''} | {len(dn)}{' ⚠' if len(dn) < MIN_N else ''} | {su:.3f} | {sd:.3f} | "
                  f"{sd / su:.2f} | {st.mean(p[2] for p in up):.1f} | {st.mean(p[2] for p in dn):.1f} |")
    md.append("\nBajada ÷ subida > 1 significa que las caídas son más rápidas que las subidas (\"escalera arriba, ascensor abajo\"): "
              "en ese caso el deslizamiento al salir con stop tiende a ser mayor que al entrar. ⚠ = n < 30: primeras lecturas, no concluye.")
    return "\n".join(md) + "\n"


if __name__ == "__main__":
    main(*sys.argv[1:5])
