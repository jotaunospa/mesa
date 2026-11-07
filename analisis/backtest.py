"""Prueba mecánica de las reglas de la Mesa contra lo que pasó después.

Toma los veredictos que calcula el código real de la Mesa (veredictos.json, una fila por
acción y foto, con el setup detectado en sus velas) y simula la operación con las velas de
1 minuto guardadas del día:
  - entrada: orden de compra en el precio de entrada del setup, si alguna vela de los 10 min
    siguientes a la foto lo toca; solo dentro de la ventana de entrada;
  - salida: stop o objetivo 2R, lo que toque primero (si una vela toca ambos, cuenta el stop);
    si no toca ninguno, cierre forzado a las 10:30 NY;
  - costos: deslizamiento supuesto de la Mesa por lado (1,5% bajo $2, 1% de $2 a $5, 0,5% sobre $5);
  - una operación por acción y día (la primera que se activa).
Esto mide reglas, no predice nada: es papel, sin spread real ni ejecuciones parciales.

Uso: python analisis/backtest.py veredictos.json DIR_DATOS
"""
import gzip, json, os, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ENTRY_START, ENTRY_END, EXIT = "09:35", "10:15", "10:30"


def slip(p):
    return 1.5 if p < 2 else 1.0 if p < 5 else 0.5


def velas(datos, dia, tk, cache={}):
    k = (dia, tk)
    if k not in cache:
        f = Path(datos) / dia / "velas" / f"{tk}.json.gz"
        cache[k] = [[datetime.fromisoformat(v[0]).strftime("%H:%M")] + v[1:] for v in json.load(gzip.open(f, "rt"))] if f.exists() else None
    return cache[k]


def simular(v, hora, s):
    """Devuelve (R neto, motivo, hora entrada) o None si no se activó."""
    e0, st, tg = s["entry"], s["stop"], s["tgt"]
    e = e0
    h0 = int(hora[:2]) * 60 + int(hora[3:])
    lim = f"{(h0 + 10) // 60:02d}:{(h0 + 10) % 60:02d}"
    ent = None
    for i, c in enumerate(v):
        t = c[0]
        if t < hora:
            continue
        if ent is None:
            if t > lim or t >= ENTRY_END:
                return None
            if c[2] >= e0:
                ent = i
                e = max(e0, c[1])  # si abre sobre la entrada, se llena a la apertura (peor precio)
                if e >= tg:
                    return None  # ya pasó el objetivo: no se persigue
                if c[3] <= st:  # la misma vela que activa toca el stop
                    return fin(e, st, "stop", t)
            continue
        if c[3] <= st:
            return fin(e, st, "stop", v[ent][0])
        if c[2] >= tg:
            return fin(e, tg, "objetivo", v[ent][0])
        if t >= EXIT:
            return fin(e, c[1], "hora", v[ent][0])
    return fin(e, v[-1][4], "fin de datos", v[ent][0]) if ent is not None else None


def fin(e, x, motivo, t):
    R = fin.entrada - fin.stop  # el riesgo se mide desde la entrada planeada
    neto = (x - e) - e * slip(e) / 100 - x * slip(x) / 100
    return round(neto / R, 2), motivo, t


import re

SALIDA = {"grupos": {}, "variantes": {}, "base": {}}  # BT_JSON=archivo.json la guarda para criterios.py

VARIANTES = {  # stop permitido (% mín, % máx) y entrada máxima sobre el VWAP (%)
    "reglas actuales (stop 2-8%, VWAP +3%)": (2, 8, 3),
    "stop hasta 12%, VWAP +12%": (2, 12, 12),
    "cualquier stop y distancia al VWAP": (0, 100, 100),
    "cualquier stop, VWAP y sin la regla del máximo del día": (0, 100, 100, False),
}


def cumple(s, var):
    """Re-evalúa el setup con otros umbrales; las demás condiciones (reciente, quiebre,
    resistencia del máximo del día) se mantienen."""
    smin, smax, vmax = var[:3]
    resistencia = var[3] if len(var) > 3 else True
    ext = None
    for c in s.get("checks", []):
        m = re.match(r"Entrada a (-?[\d.]+)% sobre el VWAP", c)
        if m:
            ext = float(m.group(1))
    if not (smin <= s["pct"] <= smax):
        return False
    if ext is not None and ext > vmax:
        return False
    if resistencia and any(c.startswith("El máximo del día") for c in s.get("fallas", [])):
        return False
    return s["live"] and s.get("broken", True)


def variantes(V, datos):
    print("\nVariantes del setup, sin los demás filtros (una operación por acción y día):")
    for nombre, var in VARIANTES.items():
        L, vistos = [], set()
        for r in V:
            s = r.get("setup")
            if not s or not (ENTRY_START <= r["hora"] < ENTRY_END) or (r["dia"], r["tk"]) in vistos:
                continue
            if not cumple(s, var):
                continue
            v = velas(datos, r["dia"], r["tk"])
            if not v:
                continue
            fin.stop, fin.entrada = s["stop"], s["entry"]
            res = simular(v, r["hora"], s)
            if res is None:
                continue
            vistos.add((r["dia"], r["tk"]))
            L.append((r["dia"], r["hora"], r["tk"], res, s["pct"]))
        SALIDA["variantes"][nombre] = [{"dia": x[0], "hora": x[1], "tk": x[2], "R": x[3][0], "motivo": x[3][1]} for x in L]
        tot = sum(x[3][0] for x in L)
        print(f"  {nombre}: {len(L)} operaciones, R neto total {tot:+.2f}")
        for x in L:
            print(f"     {x[0]} {x[1]} {x[2]:<5} stop a {x[4]}%  {x[3][0]:+.2f} R ({x[3][1]}, entra {x[3][2]})")


BASE_STOP = 8.0  # % de stop de la línea base (el máximo que permite el setup); objetivo 2R


def linea_base(V, datos):
    """Vara de comparación: entrar a ciegas en la próxima vela tras la primera foto de cada
    acción en la ventana, con stop fijo y los mismos costos. 'azar' = cualquier acción de la
    lista; 'candidato sin setup' = solo las que pasan todas las reglas de la acción."""
    print(f"\nLínea base (entrada en la apertura siguiente, stop {BASE_STOP:.0f}%, objetivo 2R, cierre 10:30):")
    for nombre, filtro in (("azar", lambda r: True),
                           ("candidato sin setup", lambda r: any(r[p]["stage"] in ("candidato", "entrada") for p in ("cons", "mom")))):
        L, vistos = [], set()
        for r in V:
            if not (ENTRY_START <= r["hora"] < ENTRY_END) or (r["dia"], r["tk"]) in vistos or not filtro(r):
                continue
            v = velas(datos, r["dia"], r["tk"])
            sig = next((c for c in v or [] if c[0] > r["hora"]), None)
            if not sig or sig[0] >= ENTRY_END:
                continue
            e = sig[1]
            st = {"entry": e, "stop": e * (1 - BASE_STOP / 100), "tgt": e * (1 + 2 * BASE_STOP / 100)}
            fin.stop, fin.entrada = st["stop"], st["entry"]
            res = simular(v, sig[0], st)
            if res is None:
                continue
            vistos.add((r["dia"], r["tk"]))
            L.append((r["dia"], sig[0], r["tk"], res))
        SALIDA["base"][nombre] = [{"dia": x[0], "hora": x[1], "tk": x[2], "R": x[3][0], "motivo": x[3][1]} for x in L]
        tot = sum(x[3][0] for x in L)
        print(f"  {nombre}: {len(L)} operaciones, R neto total {tot:+.2f}" + (f", promedio {tot / len(L):+.2f} R" if L else ""))


def main(verd, datos):
    V = json.load(open(verd))
    grupos = defaultdict(list)
    vistos = defaultdict(set)
    for r in V:
        s = r.get("setup")
        if not (s and s["ok"] and s["live"] and s.get("broken")):
            continue
        if not (ENTRY_START <= r["hora"] < ENTRY_END):
            continue
        v = velas(datos, r["dia"], r["tk"])
        if not v:
            continue
        fin.stop, fin.entrada = s["stop"], s["entry"]
        res = simular(v, r["hora"], s)
        if res is None:
            continue
        hard = {p: set(r[p]["hard"]) - {"hora"} for p in ("cons", "mom")}
        etiquetas = ["sin filtros"]
        if any(not h for h in hard.values()):
            etiquetas.append("reglas completas")
        for regla in ("noticia", "Escáner", "RVOL", "float", "precio", "dilución", "volumen", "pausa", "cambio"):
            if any(h and h <= {regla} for h in hard.values()):
                etiquetas.append(f"todas menos {regla}")
        for g in etiquetas:
            if r["tk"] in vistos[(g, r["dia"])]:
                continue
            vistos[(g, r["dia"])].add(r["tk"])
            grupos[g].append((r["dia"], r["hora"], r["tk"], res, sorted(hard["mom"] & hard["cons"]) or sorted(hard["mom"] or hard["cons"])))
    print("Prueba mecánica del setup (papel, costos supuestos):\n")
    for g in ["sin filtros", "reglas completas"] + sorted(k for k in grupos if k.startswith("todas menos")):
        L = grupos.get(g, [])
        tot = sum(x[3][0] for x in L)
        print(f"{g}: {len(L)} operaciones, R neto total {tot:+.2f}" + (f", promedio {tot / len(L):+.2f} R" if L else ""))
        for x in L:
            print(f"   {x[0]} {x[1]} {x[2]:<5} {x[3][0]:+.2f} R ({x[3][1]}, entra {x[3][2]})  bloqueada por: {', '.join(x[4]) or 'nada'}")
    SALIDA["grupos"] = {g: [{"dia": x[0], "hora": x[1], "tk": x[2], "R": x[3][0], "motivo": x[3][1]} for x in L] for g, L in grupos.items()}
    variantes(V, datos)
    linea_base(V, datos)
    if os.environ.get("BT_JSON"):
        json.dump(SALIDA, open(os.environ["BT_JSON"], "w"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
