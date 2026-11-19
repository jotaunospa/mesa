"""Estado del sistema: filas "dato rancio" por día (lib/rancio.py). Ninguna entra a candidatas ni a mediciones.

Cuenta las que el bot ya apartó al tomar la foto (clave "dato_rancio", desde el 19-11) y las que siguen
dentro de fotos anteriores (se apartan al analizar).
Uso: python analisis/rancias.py DIR_DATOS SALIDA.md DIA1 [DIA2 ...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from lib import rancio


def tipo(m: str) -> str:
    return "no calza con Nasdaq" if "no calza" in m else "bajo 5% en Nasdaq" if "Nasdaq va" in m else "% viejo de Yahoo"


def contar(datos: Path, dia: str) -> dict:
    fotos = sorted((datos / dia).glob("snap-*.json"))
    ven = [p for p in fotos if "0930" <= p.stem[5:] <= "1020"]
    out = {"dia": dia, "fotos_ventana": len(ven), "filas": 0, "rancias": 0, "primera_foto": [0, 0], "resto": [0, 0],
           "tipos": Counter(), "ejemplos": []}
    for k, p in enumerate(ven):
        s = json.loads(p.read_text())
        ok, ran = rancio.separar(s.get("filas", []))
        ran = (s.get("dato_rancio") or []) + ran
        n = len(ok) + len(ran)
        out["filas"] += n
        out["rancias"] += len(ran)
        slot = "primera_foto" if k == 0 else "resto"
        out[slot][0] += len(ran)
        out[slot][1] += n
        for r in ran:
            out["tipos"][tipo(r["motivo"])] += 1
            if len(out["ejemplos"]) < 3:
                out["ejemplos"].append(f"{p.stem[5:7]}:{p.stem[7:]} {r['ticker']}: {r['motivo']}")
    return out


def main(datos, sal_md, dias):
    datos = Path(datos)
    R = [contar(datos, d) for d in dias if (datos / d).is_dir()]
    md = ["## Estado del sistema: filas con dato rancio", "",
          "Ventana 09:30–10:20. \"Dato rancio\": Yahoo listó la acción con un % viejo (a la hora de la foto sube menos de 5%), "
          "o el cambio no calza con el precio de Nasdaq y el cierre de ayer (`lib/rancio.py`). "
          "**Ninguna entra a candidatas ni a las mediciones de este informe.**", "",
          "| Día | Fotos | Filas | Rancias | En la primera foto | En el resto | Tipo |", "|---|---|---|---|---|---|---|"]
    for r in R:
        t = ", ".join(f"{k}: {v}" for k, v in r["tipos"].most_common()) or "—"
        md.append(f"| {r['dia']} | {r['fotos_ventana']} | {r['filas']} | {r['rancias']} | "
                  f"{r['primera_foto'][0]} de {r['primera_foto'][1]} | {r['resto'][0]} de {r['resto'][1]} | {t} |")
    ej = [e for r in R for e in r["ejemplos"][:1]]
    if ej:
        md += ["", "Ejemplos: " + "; ".join(ej[:4]) + "."]
    Path(sal_md).write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
