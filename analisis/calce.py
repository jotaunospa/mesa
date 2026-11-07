"""Calce campo a campo: cada lectura del Escáner guardada en la Mesa contra la foto de origen.

Uso: python analisis/calce.py DIR_DATOS DIR_MESA_DB
  DIR_DATOS  = clon de la rama datos
  DIR_MESA_DB = carpeta con los documentos de la Mesa (r_esc_*.json, bench.json)
"""
import json, sys
from pathlib import Path

DIL = {"alta": "alto", "media": "medio", "baja": "bajo"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def comparar(doc: dict, fila: dict) -> list[str]:
    """Devuelve las diferencias entre lo que tiene la Mesa y lo que dice la foto."""
    d = []
    def mismo(campo, esperado, tol=1e-9):
        v = num(doc.get(campo))
        e = esperado
        if e is None:
            if doc.get(campo) not in ("", None):
                d.append(f"{campo}: Mesa {doc.get(campo)!r}, foto vacío")
            return
        if v is None or abs(v - float(e)) > tol * max(1, abs(float(e))):
            d.append(f"{campo}: Mesa {doc.get(campo)!r}, foto {e!r}")
    mismo("price", fila.get("price"))
    mismo("chg", fila.get("chg"))
    mismo("volM", fila.get("volM"))
    rv = fila.get("rvol_hora") if fila.get("rvol_hora") is not None else fila.get("rvol")
    mismo("rvol", None if rv is None else round(rv, 1), tol=1e-6)
    fl = fila.get("floatM")
    if fl is None and (fila.get("float_cruce") or {}).get("rango_M"):
        fl = fila["float_cruce"]["rango_M"][0]
    mismo("floatM", fl)
    mismo("dayLow", fila.get("dayLow"))
    mismo("dayHigh", fila.get("dayHigh"))
    if doc.get("dil") != DIL.get(fila.get("dil"), "medio"):
        d.append(f"dil: Mesa {doc.get('dil')}, foto {fila.get('dil')}")
    sv = fila.get("sobre_vwap_pct")
    esp = "na" if sv is None else ("sobre" if sv >= 0 else "bajo")
    if doc.get("vwap") != esp:
        d.append(f"vwap: Mesa {doc.get('vwap')}, foto {sv}")
    esc = doc.get("esc") or {}
    if bool(esc.get("apto")) != bool(fila.get("apto")):
        d.append(f"apto: Mesa {esc.get('apto')}, foto {fila.get('apto')}")
    if (esc.get("motivos") or []) != (fila.get("no_apto_por") or []):
        d.append("motivos del dictamen distintos")
    return d


def main(datos: str, mesa: str) -> None:
    datos, mesa = Path(datos), Path(mesa)
    fotos = {}
    total = malas = 0
    sin_foto = []
    for p in sorted(mesa.glob("r_esc_*.json")):
        doc = json.loads(p.read_text())
        _, _, ymd, hhmm, tk = p.stem.split("_", 4)
        fecha = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        clave = (fecha, hhmm)
        if clave not in fotos:
            f = datos / fecha / f"snap-{hhmm}.json"
            fotos[clave] = json.loads(f.read_text()) if f.exists() else None
        snap = fotos[clave]
        if snap is None:
            sin_foto.append(p.stem); continue
        fila = next((r for r in snap["filas"] if r["ticker"] == tk), None)
        if fila is None:
            sin_foto.append(p.stem); continue
        total += 1
        dif = comparar(doc, fila)
        if dif:
            malas += 1
            print(p.stem, "|", "; ".join(dif))
    b = mesa / "bench.json"
    if b.exists():
        bench = json.loads(b.read_text())
        foto = str(bench["meta"].get("foto", ""))
        snap = json.loads((datos / foto[:10] / f"snap-{foto[11:]}.json").read_text())
        for x in bench["rows"]:
            fila = next((r for r in snap["filas"] if r["ticker"] == x["ticker"]), None)
            total += 1
            dif = comparar(x, fila) if fila else ["no está en la foto"]
            if dif:
                malas += 1
                print("bench", x["ticker"], "|", "; ".join(dif))
    print(f"\n{total} filas revisadas, {malas} con diferencias, {len(sin_foto)} sin foto de origen {sin_foto[:5]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
