"""Dónde está el retorno de las candidatas: tramos del día (solo medición; no cambia reglas).

Para cada candidata de cada día (las de resultados.json: todas las que salieron en alguna foto):

  noche      cierre anterior          -> primer precio del premarket (desde las 04:00 NY)
  premarket  primer precio premarket  -> último precio antes de las 09:30
  apertura   último precio antes 09:30 -> último precio antes de las 09:35 (incluye la subasta de apertura)
  ventana    09:35                    -> 10:15
  resto      10:15                    -> cierre (último precio antes de las 16:00)

"Precio a la hora T" = cierre de la última vela de 1 min que empezó antes de T. Los tramos se
encadenan: el producto de (1 + tramo) es el retorno de cierre a cierre.

Fuentes (ya guardadas): velas/TICKER.json.gz de cierre.py (1 min con premarket), las fotos
snap-HHMM.json (cierre anterior, spread de Nasdaq, noticia, float, split) y resultados.json
(temperatura y dictamen al aparecer).

Cierre anterior: se deduce de cada foto como precio / (1 + cambio del día). Es un dato
CALCULADO desde Yahoo, no oficial. Si las fotos no concuerdan entre sí (más de 1%) o hubo un
split en los últimos 3 días, la noche queda "sin dato" con su motivo.

Uso: python analisis/tramos.py DIR_DATOS SALIDA.json SALIDA.md [AAAA-MM-DD ...]
"""
from __future__ import annotations

import gzip
import json
import statistics as st
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import costos as C

TRAMOS = ["noche", "premarket", "apertura", "ventana", "resto"]
MIN_N = 30


def cargar_velas(dia: Path, tk: str):
    f = dia / "velas" / f"{tk}.json.gz"
    if not f.exists():
        return None
    return [[v[0][11:16]] + v[1:] for v in json.load(gzip.open(f, "rt"))]


def precio_antes(v, hhmm: str):
    """Cierre de la última vela que empezó antes de hhmm (None si no hay)."""
    p = None
    for c in v:
        if c[0] >= hhmm:
            break
        if c[4] is not None:
            p = c[4]
    return p


def fotos_por_ticker(dia: Path) -> dict:
    out: dict = {}
    for p in sorted(dia.glob("snap-*.json")):
        s = json.loads(p.read_text())
        for f in s.get("filas", []):
            out.setdefault(f["ticker"], []).append(f)
    return out


def cierre_anterior(filas: list, dia: str):
    """(precio, motivo_si_falta)."""
    for f in filas:
        for sp in (f.get("split_reciente") or []):
            try:
                if date.fromisoformat(dia) - date.fromisoformat(sp["fecha"]) <= timedelta(days=3):
                    return None, f"split el {sp['fecha']}"
            except (KeyError, ValueError):
                pass
    vals = [f["price"] / (1 + f["chg"] / 100) for f in filas
            if f.get("price") and f.get("chg") is not None and f["chg"] > -100]
    if not vals:
        return None, "sin cambio del día en las fotos"
    m = st.median(vals)
    if (max(vals) - min(vals)) / m > 0.01:
        return None, "las fotos no concuerdan en el cierre anterior (>1%)"
    return m, None


def noticia(filas: list) -> str:
    ev = filas[0].get("noticias_eval")
    if not ev:
        return "sin dato (foto anterior al feed de 3 fuentes)"
    return {"confirmada": "confirmada", "no_encontrada": "no encontrada"}.get(ev.get("estado"), "sin dato (fuentes no respondieron)")


def tramo_gap(g):
    if g is None:
        return "sin dato"
    return "gap < 10%" if g < 10 else "gap 10–30%" if g < 30 else "gap 30–100%" if g < 100 else "gap ≥ 100%"


def tramo_float(fl):
    if fl is None:
        return "sin dato"
    return "float < 2 M" if fl < 2 else "float 2–10 M" if fl < 10 else "float 10–20 M" if fl < 20 else "float ≥ 20 M"


def medir_dia(dia: Path) -> list[dict]:
    res = json.loads((dia / "resultados.json").read_text()) if (dia / "resultados.json").exists() else {"candidatas": []}
    fotos = fotos_por_ticker(dia)
    out = []
    for c in res["candidatas"]:
        tk = c["ticker"]
        filas = fotos.get(tk, [])
        al = c.get("al_aparecer") or {}
        row = {"dia": dia.name, "tk": tk, "primera_hora": c["primera_hora"][11:16],
               "noticia": noticia(filas) if filas else "sin dato",
               "float": tramo_float(al.get("floatM")), "temperatura": c.get("temperatura") or "sin dato",
               "dictamen": "apta" if al.get("apto") is True else "no apta" if al.get("apto") is False else "sin dictamen",
               "spread_pct": al.get("spread_pct"), "falta": {}}
        v = cargar_velas(dia, tk)
        p0, motivo0 = cierre_anterior(filas, dia.name) if filas else (None, "sin fotos")
        if not v:
            row["falta"] = {t: "sin velas del día" for t in TRAMOS}
            row["tramos"] = {t: None for t in TRAMOS}
            out.append(row)
            continue
        pre = [x for x in v if "04:00" <= x[0] < "09:30" and x[1] is not None]
        reg = [x for x in v if "09:30" <= x[0] < "16:00"]
        p_pre0 = pre[0][1] if pre else None
        p_930 = precio_antes(v, "09:30")
        p_935, p_1015, p_cierre = precio_antes(v, "09:35"), precio_antes(v, "10:15"), precio_antes(v, "16:00")
        open_reg = reg[0][1] if reg and reg[0][0] == "09:30" else None
        puntos = {"noche": (p0, p_pre0), "premarket": (p_pre0, p_930), "apertura": (p_930, p_935),
                  "ventana": (p_935, p_1015), "resto": (p_1015, p_cierre)}
        motivos = {"noche": motivo0 or ("sin operaciones en premarket" if not pre else None),
                   "premarket": "sin operaciones en premarket" if not pre else None,
                   "apertura": None if p_930 else "sin precio antes de las 09:30",
                   "ventana": None if p_935 and p_1015 else "sin velas en la ventana",
                   "resto": None if p_1015 and p_cierre else "sin velas después de las 10:15"}
        # Sin premarket, apertura parte del cierre anterior (noche y premarket quedan juntos dentro de apertura).
        if not pre and p0 and p_935:
            puntos["apertura"] = (p0, p_935)
            motivos["apertura"] = None
            row["apertura_incluye_noche"] = True
        row["tramos"], row["neto"] = {}, {}
        for t in TRAMOS:
            a, b = puntos[t]
            if motivos[t] or not a or not b:
                row["tramos"][t] = None
                row["falta"][t] = motivos[t] or "sin precio"
                continue
            g = (b / a - 1) * 100
            row["tramos"][t] = round(g, 3)
            cost = C.ida_y_vuelta(a, b, row["spread_pct"])
            row["neto"][t] = None if cost is None else round(g - cost, 3)
        row["gap_pct"] = round((open_reg / p0 - 1) * 100, 2) if open_reg and p0 else None
        row["gap"] = tramo_gap(row["gap_pct"])
        row["cierre_a_cierre"] = round((p_cierre / p0 - 1) * 100, 3) if p0 and p_cierre else None
        row["p"] = {"cierre_ant": p0 and round(p0, 4), "pre0": p_pre0, "09:30": p_930, "09:35": p_935, "10:15": p_1015, "cierre": p_cierre}
        out.append(row)
    return out


def oficial(filas):
    """El cierre de la última vela (15:59) no es la subasta de cierre. Si la acción aparece en las fotos
    del día hábil siguiente guardado, su cierre anterior (calculado) es el cierre oficial: se usa ese."""
    dias = sorted({r["dia"] for r in filas})
    sig = {d: dias[i + 1] for i, d in enumerate(dias[:-1])}
    idx = {(r["dia"], r["tk"]): r for r in filas}
    for r in filas:
        n = idx.get((sig.get(r["dia"]), r["tk"]))
        r["cierre_fuente"] = "última vela 15:59"
        if not (n and n.get("p", {}).get("cierre_ant") and r.get("p", {}).get("10:15")):
            continue
        co, p15 = n["p"]["cierre_ant"], r["p"]["10:15"]
        r["p"]["cierre_oficial"] = co
        r["dif_ultima_vela_pct"] = round((r["p"]["cierre"] / co - 1) * 100, 2) if r["p"].get("cierre") else None
        r["tramos"]["resto"] = round((co / p15 - 1) * 100, 3)
        cost = C.ida_y_vuelta(p15, co, r["spread_pct"])
        r["neto"]["resto"] = None if cost is None else round(r["tramos"]["resto"] - cost, 3)
        if r["p"].get("cierre_ant"):
            r["cierre_a_cierre"] = round((co / r["p"]["cierre_ant"] - 1) * 100, 3)
        r["cierre_fuente"] = "cierre oficial (calculado de la foto del día siguiente)"


def resumen(vals):
    vals = [x for x in vals if x is not None]
    if not vals:
        return "—"
    return f"{st.mean(vals):+.2f} / {st.median(vals):+.2f}"


def tabla(filas, clave, titulo):
    lin = [f"\n**{titulo}** (promedio / mediana, %; bruto)\n", "| Grupo | n | " + " | ".join(TRAMOS) + " | ventana neta |",
           "|---|---|" + "---|" * len(TRAMOS) + "---|"]
    grupos: dict = {}
    for r in filas:
        grupos.setdefault(r.get(clave) or "sin dato", []).append(r)
    for g in sorted(grupos):
        L = grupos[g]
        n = sum(1 for r in L if r["tramos"].get("ventana") is not None)
        aviso = " ⚠" if n < MIN_N else ""
        lin.append(f"| {g} | {n}{aviso} | " + " | ".join(resumen([r["tramos"].get(t) for r in L]) for t in TRAMOS)
                   + f" | {resumen([r.get('neto', {}).get('ventana') for r in L])} |")
    return "\n".join(lin)


def markdown(filas) -> str:
    vent = [r["tramos"]["ventana"] for r in filas if r["tramos"].get("ventana") is not None]
    neta = [r["neto"]["ventana"] for r in filas if r.get("neto", {}).get("ventana") is not None]
    md = ["## Tramos del día: ¿dónde está el retorno de las candidatas?\n",
          f"{len(filas)} candidatas (todas las que salieron en alguna foto), {len({r['dia'] for r in filas})} días. "
          "Retorno de comprar al inicio del tramo y vender al final. ⚠ = muestra chica (n < 30), no concluye.\n",
          C.nota() + "\n"]
    if vent:
        pos = sum(1 for x in vent if x > 0)
        md.append(f"**a) Ventana (09:35 → 10:15):** n = {len(vent)}; promedio bruto {st.mean(vent):+.2f}%, mediana {st.median(vent):+.2f}%, "
                  f"{pos} de {len(vent)} positivas. Neto de costos (n = {len(neta)}): "
                  + (f"promedio {st.mean(neta):+.2f}%, mediana {st.median(neta):+.2f}%." if neta else "sin dato.")
                  + (" **Muestra chica, no concluye.**" if len(vent) < MIN_N else ""))
    chica = [r["neto"]["ventana"] for r in filas if r.get("neto", {}).get("ventana") is not None and (r["spread_pct"] or 99) <= 3]
    if chica:
        md.append(f"Solo las que tenían spread ≤ 3% (las que el Escáner deja pasar por spread): n = {len(chica)}, ventana neta "
                  f"promedio {st.mean(chica):+.2f}%, mediana {st.median(chica):+.2f}%." + (" Muestra chica, no concluye." if len(chica) < MIN_N else ""))
    difs = [abs(r["dif_ultima_vela_pct"]) for r in filas if r.get("dif_ultima_vela_pct") is not None]
    if difs:
        md.append(f"\nCierre del día: si la acción salió también al día siguiente se usa su cierre oficial (calculado de esa foto); si no, "
                  f"la última vela de 1 min. En {len(difs)} acciones con ambos, la última vela difería del cierre oficial en "
                  f"{st.median(difs):.2f}% (mediana; máximo {max(difs):.2f}%).")
    md.append(tabla(filas, "_todas", "Todas"))
    for clave, t in (("noticia", "Por noticia"), ("gap", "Por tamaño del gap"), ("float", "Por float"),
                     ("temperatura", "Por temperatura del día"), ("dictamen", "Por dictamen del Escáner")):
        md.append(tabla(filas, clave, t))
    faltas: dict = {}
    for r in filas:
        for t, m in r["falta"].items():
            faltas[(t, m)] = faltas.get((t, m), 0) + 1
    if faltas:
        md.append("\n**Sin dato (no se rellena):** " + "; ".join(f"{t}: {m} ({n})" for (t, m), n in sorted(faltas.items())))
    return "\n".join(md) + "\n"


def main(datos, sal_json, sal_md, dias):
    datos = Path(datos)
    dias = dias or sorted(p.name for p in datos.glob("20??-??-??") if (p / "resultados.json").exists())
    filas = []
    for d in dias:
        filas += medir_dia(datos / d)
    oficial(filas)
    for r in filas:
        r["_todas"] = "todas"
    json.dump(filas, open(sal_json, "w"), ensure_ascii=False)
    Path(sal_md).write_text(markdown(filas))
    print(f"tramos: {len(filas)} candidatas en {len(dias)} días")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
