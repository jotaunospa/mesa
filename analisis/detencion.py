"""Criterio de detención (claude/mesa-propuesta-rendimiento.md, sección H; aprobado el 07-11, antes de ver datos).

Pregunta: después de 4 semanas completas de ventana, ¿el retorno neto de la ventana (09:35 → 10:15) de
las candidatas aptas tiene su intervalo de confianza de 90% entero bajo cero?

Definiciones (fijas):
- Semana completa de ventana: semana ISO en la que cada día hábil de la bolsa de EE.UU. (lunes a viernes,
  sin los feriados de NYSE de abajo) tiene fotos dentro de la ventana (09:31–10:20 NY).
- Candidata apta: dictamen APTA del Escáner en alguna foto tomada hasta las 09:35 NY. Así se elige con lo
  que se sabía al empezar la ventana (sin mirar el futuro). Una por acción y día.
- Retorno neto: el de tramos.py (comprar a las 09:35, vender a las 10:15, menos spread de Nasdaq,
  deslizamiento escalonado y comisión supuesta). Sin spread: queda fuera, contado como "sin dato".
- Intervalo de 90%: sobre los PROMEDIOS DIARIOS (las acciones del mismo día se mueven juntas; tratarlas
  como independientes daría un intervalo falsamente angosto). t de Student con d − 1 grados de libertad,
  d = días con al menos una apta.
- Estado: "midiendo" (menos de 4 semanas completas) / "se cumple" (intervalo entero bajo 0) /
  "no concluye" (cruza el 0; se revisa de nuevo a las 8 semanas) / "no se cumple" (intervalo entero sobre 0;
  no prueba ventaja: es comprar y mantener la ventana, sin setup).
"""
from __future__ import annotations

import json
import statistics as st
from datetime import date, timedelta
from pathlib import Path

SEMANAS_MIN, SEMANAS_REVISION = 4, 8
FOTO_MAX = "0935"
# Feriados de NYSE (calendario publicado). Agregar los años siguientes cuando corresponda.
FERIADOS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
# t de Student, cola 5% (intervalo de 90% de dos colas), por grados de libertad.
T95 = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
       11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753, 16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729,
       20: 1.725, 25: 1.708, 30: 1.697, 40: 1.684, 60: 1.671, 120: 1.658}


def t95(gl: int) -> float:
    if gl in T95:
        return T95[gl]
    menores = [k for k in T95 if k <= gl]
    return T95[max(menores)] if menores else T95[1]


def con_ventana(datos: Path, dia: str) -> bool:
    return any("0931" <= p.stem[5:] <= "1020" for p in (datos / dia).glob("snap-*.json"))


def semanas_completas(datos: Path) -> tuple[list[str], list[str]]:
    """(semanas completas 'AAAA-Www', semanas con días faltantes y cuáles)."""
    dias = sorted(p.name for p in datos.glob("20??-??-??") if p.is_dir())
    if not dias:
        return [], []
    ultimo = date.fromisoformat(dias[-1])  # referencia: el último día guardado (determinista, no el reloj)
    semanas: dict = {}
    for d in dias:
        f = date.fromisoformat(d)
        semanas.setdefault(f.isocalendar()[:2], None)
    ok, incompletas = [], []
    for (y, w) in sorted(semanas):
        lunes = date.fromisocalendar(y, w, 1)
        habiles = [lunes + timedelta(i) for i in range(5) if (lunes + timedelta(i)).isoformat() not in FERIADOS]
        if habiles[-1] > ultimo:
            continue  # semana en curso: todavía no se puede juzgar
        faltan = [h.isoformat() for h in habiles if not (datos / h.isoformat()).exists() or not con_ventana(datos, h.isoformat())]
        (incompletas.append(f"{y}-W{w:02d} (sin ventana: {', '.join(faltan)})") if faltan else ok.append(f"{y}-W{w:02d}"))
    return ok, incompletas


def aptas_hasta(datos: Path, dia: str) -> set[str]:
    out = set()
    for p in sorted((datos / dia).glob("snap-*.json")):
        if not ("0931" <= p.stem[5:] <= FOTO_MAX):
            continue
        for f in json.loads(p.read_text()).get("filas", []):
            if f.get("apto") is True:
                out.add(f["ticker"])
    return out


def evaluar(tramos: list, datos: Path) -> dict:
    datos = Path(datos)
    sem_ok, sem_inc = semanas_completas(datos)
    dias_ok = set()
    for s in sem_ok:
        y, w = int(s[:4]), int(s[6:])
        dias_ok |= {(date.fromisocalendar(y, w, 1) + timedelta(i)).isoformat() for i in range(5)}
    por_dia, sin_dato, n = {}, 0, 0
    aptas_cache: dict = {}
    for r in tramos:
        d = r["dia"]
        if d not in dias_ok:
            continue
        if d not in aptas_cache:
            aptas_cache[d] = aptas_hasta(datos, d)
        if r["tk"] not in aptas_cache[d]:
            continue
        x = (r.get("neto") or {}).get("ventana")
        if x is None:
            sin_dato += 1
            continue
        por_dia.setdefault(d, []).append(x)
        n += 1
    medias = [st.mean(v) for v in por_dia.values()]
    out = {"semanas": len(sem_ok), "semanas_lista": sem_ok, "incompletas": sem_inc, "n": n, "dias": len(medias),
           "sin_dato": sin_dato, "media": None, "ic90": None}
    if len(medias) >= 2:
        m, s = st.mean(medias), st.stdev(medias)
        h = t95(len(medias) - 1) * s / len(medias) ** 0.5
        out.update(media=round(m, 2), ic90=[round(m - h, 2), round(m + h, 2)])
    elif medias:
        out["media"] = round(medias[0], 2)
    if out["semanas"] < SEMANAS_MIN:
        out["estado"] = "midiendo"
    elif not out["ic90"]:
        out["estado"] = "no concluye"
    elif out["ic90"][1] < 0:
        out["estado"] = "se cumple"
    elif out["ic90"][0] > 0:
        out["estado"] = "no se cumple"
    else:
        out["estado"] = "no concluye"
    return out


def markdown(ev: dict) -> str:
    ic = f"[{ev['ic90'][0]:+.2f}%, {ev['ic90'][1]:+.2f}%]" if ev["ic90"] else "sin intervalo (menos de 2 días)"
    md = ["## Criterio de detención (sección H, fijado el 07-11)\n",
          f"**Estado: {ev['estado'].upper()}.**\n",
          "| Semanas completas de ventana | Días con aptas | n aptas | Sin dato (sin spread) | Retorno neto medio de la ventana | IC 90% (sobre promedios diarios) |",
          "|---|---|---|---|---|---|",
          f"| {ev['semanas']} de {SEMANAS_MIN} | {ev['dias']} | {ev['n']} | {ev['sin_dato']} | "
          f"{'—' if ev['media'] is None else format(ev['media'], '+.2f') + '%'} | {ic} |"]
    if ev["incompletas"]:
        md.append(f"\nSemanas que no cuentan: {'; '.join(ev['incompletas'])}.")
    if ev["n"] < 30:
        md.append("\nPrimeras lecturas, no concluye (n < 30).")
    md.append({
        "midiendo": f"\nFaltan {SEMANAS_MIN - ev['semanas']} semanas completas para evaluar el criterio. Se sigue midiendo.",
        "no concluye": f"\nEl intervalo cruza el 0: no se puede concluir. Se sigue midiendo y se revisa de nuevo a las {SEMANAS_REVISION} semanas.",
        "se cumple": "\n**Se cumple:** el retorno neto de la ventana de las aptas está bajo cero con 90% de confianza. La Mesa sigue "
                     "registrando, pero se dejan de proponer ajustes de reglas de entrada para esta ventana. La pregunta pasa a ser "
                     "si hay algo que operar en este horario: se evalúa otro tramo u otro tipo de acción con los mismos datos.",
        "no se cumple": "\nEl intervalo está entero sobre 0. No prueba ventaja: mide comprar y mantener la ventana, sin setup ni stop.",
    }[ev["estado"]])
    md.append("\nDefinición: apta = APTA del Escáner en una foto hasta las 09:35; compra a las 09:35 y venta a las 10:15; neto de spread, "
              "deslizamiento y comisión supuesta; intervalo t de 90% sobre el promedio de cada día.")
    return "\n".join(md) + "\n"
