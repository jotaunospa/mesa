"""Espera una foto nueva del Escáner y deja listo el lote para la base de datos de la Mesa.

Lo usa la tarea programada "Escáner Mesa: llenar la Mesa". Lee la rama `datos` por la API
de GitHub (gh), así que funciona desde cualquier sesión de Claude con acceso al repo.

Uso:
  python integracion/a_mesa_lote.py --ultima HHMM --out DIR [--esperar 420]

Imprime una sola línea JSON:
  {"nueva": true, "foto": "0945", "hora": "09:45 NY", "lote": "DIR/lote.json", "filas": 20, "aptas": 3}
  {"nueva": false, "ultima_foto": "0942", "min_desde_ultima": 5, "dia_con_fotos": true}
lote.json = lista de escrituras para ArtifactData "batch" (colección data/users/me). La de
"bench" va primera y le falta if_version: hay que leer su versión antes de escribir.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = "jotaunospa/mesa"
NY = ZoneInfo("America/New_York")


def raw(path: str):
    r = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{path}?ref=datos",
                        "-H", "Accept: application/vnd.github.raw"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def ultima_foto(hoy: str) -> str | None:
    idx = raw("index.json") or {}
    for d in idx.get("dias", []):
        if d.get("fecha") == hoy and d.get("fotos"):
            return d["fotos"][-1]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ultima", default="", help="última foto ya escrita en la Mesa (HHMM)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--esperar", type=int, default=420, help="segundos máximos de espera")
    a = ap.parse_args()
    ahora = datetime.now(NY)
    hoy = ahora.strftime("%Y-%m-%d")
    fin = time.time() + a.esperar
    foto = None
    while True:
        foto = ultima_foto(hoy)
        if foto and foto != a.ultima:
            break
        if time.time() >= fin:
            m = None
            if foto:
                h = datetime.now(NY)
                m = h.hour * 60 + h.minute - (int(foto[:2]) * 60 + int(foto[2:]))
            print(json.dumps({"nueva": False, "ultima_foto": foto, "min_desde_ultima": m, "dia_con_fotos": bool(foto)}))
            return
        time.sleep(30)
    m = None
    for _ in range(6):  # mesa.json se publica en el mismo commit; por si el CDN de la API tarda
        m = raw(f"{hoy}/mesa.json")
        if m and m.get("foto") == foto:
            break
        time.sleep(10)
    if not m or m.get("foto") != foto:
        print(json.dumps({"nueva": False, "ultima_foto": foto, "error": "mesa.json no corresponde a la última foto"}))
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.json"):
        p.unlink()
    (out / "bench.json").write_text(json.dumps(m["bench"], ensure_ascii=False))
    writes = [{"op": "set", "collection": "data/users/me", "doc_id": "bench", "file_path": str(out / "bench.json")}]
    for r in m["reads"]:
        f = out / f"{r['id']}.json"
        f.write_text(json.dumps(r, ensure_ascii=False))
        writes.append({"op": "set", "collection": "data/users/me", "doc_id": r["id"], "file_path": str(f)})
    (out / "lote.json").write_text(json.dumps(writes, ensure_ascii=False, indent=1))
    filas = m["bench"]["rows"]
    print(json.dumps({"nueva": True, "foto": foto, "hora": m["bench"]["meta"]["time"], "lote": str(out / "lote.json"),
                      "filas": len(filas), "aptas": sum(1 for x in filas if x["esc"]["apto"])}))


if __name__ == "__main__":
    sys.exit(main())
