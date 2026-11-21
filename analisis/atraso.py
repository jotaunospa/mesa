"""¿Cuánto cambia el resultado si la entrada llega tarde? (medición, no cambia reglas; 21-11)

Las simulaciones de backtest.py entran desde la vela del minuto de la foto, como si la reacción
fuera instantánea. En la realidad la foto se publica 1,7 a 4,3 min después de su hora (medido en
los commits de `datos`, ventana 17–20-11: mediana 134 s, p90 193 s, máximo 256 s), y a eso se suma
el refresco de la página (≤ 20 s), la copia a la Mesa y el tiempo de decidir.

Este script repite exactamente la simulación de backtest.py (mismo setup, stop, objetivo 2R, costos
y cierre 10:30) corriendo el inicio de la orden D minutos: la orden se pone en hora + D y vive 10 min
desde ahí. D = 0 es lo que reporta el informe. La línea base al azar se corre igual (próxima vela
después de hora + D), para que la comparación sea pareja.

Además: la latencia real por foto (commits "foto snap-HHMM.json" de la rama datos) si se pasa
un archivo con `git log --format='%cI|%s'` como tercer argumento.

Uso: python analisis/atraso.py veredictos.json DIR_DATOS [commits.txt] > atraso.md
"""
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import json
import backtest as B

ATRASOS = [0, 1, 2, 3, 4, 5]
NY = ZoneInfo("America/New_York")


def mas(hora, d):
    h = int(hora[:2]) * 60 + int(hora[3:]) + d
    return f"{h // 60:02d}:{h % 60:02d}"


def setups(V, datos, var, d):
    """Como backtest.variantes, con la orden corrida d minutos. {(dia, tk): (hora, R, motivo)}"""
    out = {}
    for r in V:
        s = r.get("setup")
        if not s or not (B.ENTRY_START <= r["hora"] < B.ENTRY_END) or (r["dia"], r["tk"]) in out:
            continue
        if not B.cumple(s, var):
            continue
        v = B.velas(datos, r["dia"], r["tk"])
        if not v:
            continue
        h = mas(r["hora"], d)
        if h >= B.ENTRY_END:
            continue
        B.fin.stop, B.fin.entrada = s["stop"], s["entry"]
        res = B.simular(v, h, s)
        if res is None:
            continue
        out[(r["dia"], r["tk"])] = (r["hora"], res[0], res[1])
    return out


def base(V, datos, d):
    out = {}
    for r in V:
        if not (B.ENTRY_START <= r["hora"] < B.ENTRY_END) or (r["dia"], r["tk"]) in out:
            continue
        v = B.velas(datos, r["dia"], r["tk"])
        h = mas(r["hora"], d)
        sig = next((c for c in v or [] if c[0] > h), None)
        if not sig or sig[0] >= B.ENTRY_END:
            continue
        e = sig[1]
        stp = {"entry": e, "stop": e * (1 - B.BASE_STOP / 100), "tgt": e * (1 + 2 * B.BASE_STOP / 100)}
        B.fin.stop, B.fin.entrada = stp["stop"], stp["entry"]
        res = B.simular(v, sig[0], stp)
        if res is None:
            continue
        out[(r["dia"], r["tk"])] = (r["hora"], res[0], res[1])
    return out


def resumen(D):
    R = [x[1] for x in D.values()]
    if not R:
        return "0", "—", "—", "—"
    return str(len(R)), f"{sum(R):+.2f}", f"{st.mean(R):+.2f}", f"{sum(1 for x in R if x > 0)} de {len(R)}"


def latencia(archivo):
    lat = defaultdict(list)
    for l in open(archivo):
        if "|foto snap-" not in l:
            continue
        t, s = l.strip().split("|", 1)
        t = datetime.fromisoformat(t).astimezone(NY)
        hh = s.split("snap-")[1][:4]
        if not ("0931" <= hh <= "1019"):
            continue
        d = (t - t.replace(hour=int(hh[:2]), minute=int(hh[2:]), second=0)).total_seconds()
        if 0 <= d <= 900:
            lat[t.date().isoformat()].append((hh, d))
    return lat


def main(verd, datos, commits=None):
    V = json.load(open(verd))
    print("## Atraso de publicación: cuánto cambia el resultado si la entrada llega tarde\n")
    print("Medición, no cambia reglas. Misma simulación que la prueba mecánica (setup, stop, objetivo 2R, "
          "costos supuestos, cierre 10:30), con la orden puesta D minutos después de la hora de la foto. "
          "D = 0 es lo que muestra el resto del informe. Papel; no predice nada.\n")
    if commits:
        lat = latencia(commits)
        todos = sorted(d for v in lat.values() for _, d in v)
        if todos:
            q = lambda p: round(todos[int(p * (len(todos) - 1))])
            print(f"**Latencia medida** (hora de la foto → commit en `datos`, fotos de la ventana, {len(lat)} días, "
                  f"n = {len(todos)}): mediana {q(.5)} s, p75 {q(.75)} s, p90 {q(.9)} s, máximo {round(todos[-1])} s. "
                  "La primera foto del día es la más lenta (" + ", ".join(
                      f"{k[8:10]}-{k[5:7]}: {round(d)} s" for k, v in sorted(lat.items()) for h, d in v if h == "0931") +
                  "). Hay que sumar el refresco de la página (≤ 20 s), la copia a la Mesa y el tiempo de decidir.\n")
    filas = [("Línea base al azar", None)] + list(B.VARIANTES.items())
    for nombre, var in filas:
        print(f"**{nombre}**\n")
        print("| Atraso | n | R neto total | R neto promedio | positivas |")
        print("|---|---|---|---|---|")
        res = {}
        for d in ATRASOS:
            res[d] = base(V, datos, d) if var is None else setups(V, datos, var, d)
            n, tot, prom, pos = resumen(res[d])
            print(f"| {d} min | {n} | {tot} | {prom} | {pos} |")
        # pareado: las mismas operaciones con 0 y 3 min
        comunes = set(res[0]) & set(res[3])
        if comunes:
            a = [res[0][k][1] for k in comunes]
            b = [res[3][k][1] for k in comunes]
            print(f"\nMismas operaciones con 0 y 3 min (n = {len(comunes)}): {st.mean(a):+.2f} → {st.mean(b):+.2f} R promedio. "
                  f"Con 3 min se pierden {len(set(res[0]) - set(res[3]))} (no se activan o ya pasaron el objetivo) "
                  f"y aparecen {len(set(res[3]) - set(res[0]))}." + (" Primeras lecturas, no concluye (n < 30)." if len(comunes) < 30 else ""))
        print()


if __name__ == "__main__":
    main(*sys.argv[1:4])
