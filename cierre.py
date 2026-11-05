"""Registro de resultados al cierre: qué pasó DESPUÉS con cada candidata.

Para cada ticker que apareció en alguna foto del día (las elegidas y también las
descartadas), toma su primera aparición y mide con velas de 1 minuto:
  subida máxima y caída máxima desde ahí, precio a +15/+30/+60 min, cierre,
  y a qué hora tocó el máximo.

Guarda además las velas de 1 minuto del día (Yahoo solo conserva ~7 días), para
poder recalcular después con reglas nuevas.

Esto es lo que permite saber qué señales sirven. Sin esto, cada indicador nuevo es ruido.

Uso: python cierre.py [AAAA-MM-DD]   (por defecto hoy, hora NY)
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from screener import DATOS, NY, publicar, r, solo_regular


def primera_aparicion(dia: Path) -> dict:
    vistas: dict = {}
    for p in sorted(dia.glob("snap-*.json")):
        s = json.loads(p.read_text())
        for f in s.get("filas", []):
            tk = f["ticker"]
            if tk not in vistas:
                vistas[tk] = {"hora": s["hora_ny"], "precio": f["price"], "fila": f,
                              "temperatura": s.get("temperatura", {}).get("nivel"), "apariciones": 0}
            vistas[tk]["apariciones"] += 1
    return vistas


def medir(tk: str, desde_iso: str, precio0: float, carpeta_velas: Path) -> dict:
    v = yf.Ticker(tk).history(period="1d", interval="1m", prepost=True)
    if v is None or v.empty:
        return {"estado": "sin velas"}
    v.index = v.index.tz_convert(NY)
    with gzip.open(carpeta_velas / f"{tk}.json.gz", "wt") as fh:
        json.dump([[i.isoformat(), r(x.Open, 4), r(x.High, 4), r(x.Low, 4), r(x.Close, 4), int(x.Volume)]
                   for i, x in v.iterrows()], fh)
    desde = datetime.fromisoformat(desde_iso)
    reg = solo_regular(v)
    tras = reg[reg.index >= desde]
    if tras.empty or not precio0:
        return {"estado": "sin velas después de la aparición"}

    def a(min_):
        t = desde.timestamp() + min_ * 60
        sub = tras[[i.timestamp() >= t for i in tras.index]]
        return r((float(sub["Close"].iloc[0]) / precio0 - 1) * 100) if not sub.empty else None

    imax = tras["High"].idxmax()
    return {
        "estado": "ok",
        "max_subida_pct": r((float(tras["High"].max()) / precio0 - 1) * 100),
        "max_caida_pct": r((float(tras["Low"].min()) / precio0 - 1) * 100),
        "hora_max": imax.strftime("%H:%M"),
        "a_15m_pct": a(15), "a_30m_pct": a(30), "a_60m_pct": a(60),
        "cierre_pct": r((float(tras["Close"].iloc[-1]) / precio0 - 1) * 100),
    }


def main(fecha: str | None = None) -> None:
    ahora = datetime.now(NY)
    if fecha is None and not os.environ.get("FORZAR") and ahora.hour * 60 + ahora.minute < 16 * 60 + 5:
        print(f"todavía no cierra el mercado ({ahora:%H:%M} NY)"); return
    fecha = fecha or ahora.strftime("%Y-%m-%d")
    dia = DATOS / fecha
    if not dia.exists():
        print(f"no hay fotos para {fecha}"); return
    velas = dia / "velas"; velas.mkdir(exist_ok=True)
    out = []
    for tk, v in primera_aparicion(dia).items():
        try:
            res = medir(tk, v["hora"], v["precio"], velas)
        except Exception as e:
            res = {"estado": f"error {type(e).__name__}: {e}"[:200]}
        f = v["fila"]
        out.append({"ticker": tk, "primera_hora": v["hora"], "precio_inicial": v["precio"],
                    "apariciones": v["apariciones"], "temperatura": v["temperatura"],
                    "al_aparecer": {k: f.get(k) for k in ("chg", "volM", "rvol", "rvol_hora", "floatM",
                                                          "rotaciones", "sobre_vwap_pct", "hod_dist_pct",
                                                          "surge5m", "ssr", "dil", "veto", "news",
                                                          "en_filtro_mesa", "float_muy_bajo", "pais_riesgo",
                                                          "spread_pct")},
                    "resultado": res})
    (dia / "resultados.json").write_text(json.dumps({"fecha": fecha, "candidatas": out},
                                                    ensure_ascii=False, indent=1, default=str))
    for o in out:
        res = o["resultado"]
        print(f"{o['ticker']:<6} {o['primera_hora'][11:16]} ${o['precio_inicial']}  "
              f"máx {res.get('max_subida_pct')}% ({res.get('hora_max')})  mín {res.get('max_caida_pct')}%  "
              f"+15 {res.get('a_15m_pct')}  +30 {res.get('a_30m_pct')}  +60 {res.get('a_60m_pct')}  "
              f"cierre {res.get('cierre_pct')}  [{res['estado']}]")
    publicar(f"resultados {fecha}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
