"""Tabla del explorador: una fila por acción y día, con lo que el Escáner sabía al verla por primera vez
(09:30–10:20 NY) y qué pasó después. Base: prototipo del 17-11 (docs del proyecto, mesa-explorador-prototipo).

- Excluye filas "dato rancio" (lib/rancio.py): una acción cuenta desde su primera aparición válida.
- Acumulada: lee candidatas.json si existe, reemplaza los días que están en DIR_DATOS y conserva los demás
  (los días que ya rotaron a los Releases no se pierden).
- Columnas que se suman a medida que existen (si falta el dato: "sin dato", nunca un valor por defecto):
  retorno neto hasta 10:15 (costos.py; sin spread → sin dato), medias 9/20/VWAP al primer avistamiento
  (lib/ema.py, velas desde el premarket hasta la vela anterior), ruido de StockTwits de esa foto, veredicto
  de cada perfil según el registro del viernes (analisis/registro/), y diferencia contra la candidata
  promedio del día (hasta 10:15, bruto).
Todo en % y bruto salvo donde dice neto. Papel; no predice nada.
Uso: python analisis/armar_tabla.py DIR_DATOS SALIDA.json
"""
from __future__ import annotations

import gzip
import json
import statistics as st
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))
import costos as C
from lib import rancio
from lib.ema import ema


def hm(t: str) -> int:
    return int(t[11:13]) * 60 + int(t[14:16])


def r2(x):
    return None if x is None else round(x, 2)


def medias(v: list, m0: int) -> str:
    antes = [x for x in v if x[0] < m0]
    if len(antes) < 20:
        return "sin dato"
    cl = [x[4] for x in antes]
    e9, e20 = ema(cl, 9)[-1], ema(cl, 20)[-1]
    reg = [x for x in antes if x[0] >= 570]
    vv = sum(x[5] for x in reg)
    if not vv:
        return "sin dato"
    vw = sum((x[2] + x[3] + x[4]) / 3 * x[5] for x in reg) / vv
    return "ordenado" if e9 > e20 > vw else "no ordenado"


def ruido(f: dict) -> str:
    s = f.get("stocktwits") or {}
    if not s or s.get("error") or ("cambio" not in s and not s.get("saturado")):
        return "sin dato"
    return "alto" if s.get("saturado") or (s.get("cambio") or 0) >= 2 else "normal"


def perfiles(dia: str) -> list | None:
    """Eventos del registro del viernes de ese día; None si el día no está registrado."""
    p = AQUI / "registro" / f"{dia}.jsonl"
    if not p.exists():
        return None
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def etapa(reg: list, tk: str, perfil: str, desde: str) -> str:
    """Etapa de ese perfil en la foto del primer avistamiento válido.
    El registro guarda GO, LISTO, VIGILAR y CASI; si esa foto no aparece, fue NO-GO."""
    ev = [e["etapa"] for e in reg if e["tk"] == tk and e["perfil"] == perfil and e["hora"] == desde]
    return ev[0] if ev else "NO-GO"


def armar_dia(day: Path) -> list[dict]:
    snaps = sorted(s for s in day.glob("snap-*.json") if 930 <= int(s.stem[5:]) <= 1020)
    vistos = {}
    for s in snaps:
        d = json.loads(s.read_text())
        for f in rancio.separar(d.get("filas", []))[0]:
            if f["ticker"] not in vistos:
                vistos[f["ticker"]] = (s.stem[5:], f, d.get("temperatura"))
    reg = perfiles(day.name)
    filas = []
    for tk, (hhmm, f, temp) in vistos.items():
        fc = f.get("float_cruce") or {}
        ne = f.get("noticias_eval") or {}
        apta = "apta" if f.get("apto") is True else "no apta" if f.get("apto") is False else "sin dato"
        fila = dict(dia=day.name, ticker=tk, visto=f"{hhmm[:2]}:{hhmm[2:]}",
                    precio=f.get("price"), cambio_dia=r2(f.get("chg")), vol_M=r2(f.get("volM")), rvol=r2(f.get("rvol")),
                    float_M=r2(f.get("floatM")), float_estado=fc.get("estado") if isinstance(fc, dict) else None,
                    dilucion=f.get("dil") or "sin dato",
                    noticia=ne.get("estado") if isinstance(ne, dict) and ne.get("estado") else ("si" if f.get("news") else "no"),
                    sobre_vwap=r2(f.get("sobre_vwap_pct")), dist_max_dia=r2(f.get("hod_dist_pct")),
                    short_pct=r2(f.get("short_pct_float")), spread=r2(f.get("spread_pct")), pais=f.get("pais"),
                    apta=apta, filtro_mesa="si" if f.get("en_filtro_mesa") else "no",
                    temperatura=(temp or {}).get("nivel") if isinstance(temp, dict) else temp,
                    ruido_stocktwits=ruido(f))
        if reg is None:
            fila["perfil_conservador"] = fila["perfil_momentum"] = "sin dato (se registra el viernes)"
        else:
            fila["perfil_conservador"] = etapa(reg, tk, "cons", fila["visto"])
            fila["perfil_momentum"] = etapa(reg, tk, "mom", fila["visto"])
        p = day / "velas" / f"{tk}.json.gz"
        fila["medias_9_20_vwap"] = "sin dato"
        if p.exists():
            v = [(hm(x[0]), *x[1:]) for x in json.load(gzip.open(p, "rt"))]
            m0 = int(hhmm[:2]) * 60 + int(hhmm[2:])
            fila["medias_9_20_vwap"] = medias(v, m0)
            tras = [x for x in v if m0 <= x[0] < 615]
            if tras and tras[0][1]:
                e = tras[0][1]
                x1015 = tras[-1][4]
                fila["hasta_1015"] = r2((x1015 / e - 1) * 100)
                cost = C.ida_y_vuelta(e, x1015, f.get("spread_pct"))
                fila["hasta_1015_neto"] = None if cost is None else r2((x1015 / e - 1) * 100 - cost)
                fila["mejor_hasta_1015"] = r2((max(x[2] for x in tras) / e - 1) * 100)
                fila["peor_hasta_1015"] = r2((min(x[3] for x in tras) / e - 1) * 100)
                cierre = [x for x in v if x[0] < 960]
                fila["hasta_cierre"] = r2((cierre[-1][4] / e - 1) * 100) if cierre else None
        filas.append(fila)
    con = [x["hasta_1015"] for x in filas if x.get("hasta_1015") is not None]
    if con:
        prom = st.mean(con)
        for x in filas:
            if x.get("hasta_1015") is not None:
                x["vs_promedio_dia"] = r2(x["hasta_1015"] - prom)
    return filas


def main(base, salida):
    base, salida = Path(base), Path(salida)
    dias = sorted(p for p in base.glob("20??-??-??") if p.is_dir() and any(p.glob("snap-*.json")))
    previas = json.loads(salida.read_text()) if salida.exists() else []
    nombres = {d.name for d in dias}
    filas = [x for x in previas if x.get("dia") not in nombres]
    for d in dias:
        filas += armar_dia(d)
    filas.sort(key=lambda x: (x["dia"], x["visto"], x["ticker"]))
    salida.write_text(json.dumps(filas, ensure_ascii=False, separators=(",", ":")))
    print(len(filas), "filas;", sum("hasta_1015" in f for f in filas), "con resultado;", len({x['dia'] for x in filas}), "días")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
