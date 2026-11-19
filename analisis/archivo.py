"""Rotación de la rama `datos`: quedan los últimos 30 días de mercado; lo anterior va a Releases.

- rotar DIR_DATOS [--dias 30] [--probar]: comprime cada día viejo (AAAA-MM-DD.tar.gz), lo sube al Release
  "datos-AAAA-MM" del repo (lo crea si no existe), verifica que el archivo quedó arriba con el mismo
  tamaño y recién entonces lo borra de la carpeta. No hace commit: eso lo hace el workflow con
  lib/git_datos.empujar. --probar solo dice qué haría.
- completar DIR_DATOS: baja de los Releases los días que no estén en la carpeta (para el análisis).

La historia de git de `datos` no se reescribe (otros procesos escriben ahí en vivo): lo que se achica es
la carpeta que se clona y lo que lee la web. Usa `gh` (GH_TOKEN en Actions; en las sesiones de Claude, gh api funciona).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "jotaunospa/mesa")
DIA = re.compile(r"^20\d\d-\d\d-\d\d$")


def gh(*args, check=True) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout


def dias_de_mercado(datos: Path) -> list[str]:
    return sorted(p.name for p in datos.iterdir() if p.is_dir() and DIA.match(p.name) and any(p.glob("snap-*.json")))


def assets(tag: str) -> dict:
    out = gh("api", f"repos/{REPO}/releases/tags/{tag}", check=False)
    try:
        return {a["name"]: a["size"] for a in json.loads(out).get("assets", [])}
    except (ValueError, AttributeError):
        return {}


def asegurar_release(tag: str) -> None:
    try:
        if json.loads(gh("api", f"repos/{REPO}/releases/tags/{tag}", check=False) or "{}").get("id"):
            return
    except ValueError:
        pass
    gh("release", "create", tag, "-R", REPO, "--title", f"Datos de la Mesa {tag[6:]}",
       "--notes", "Días de mercado rotados desde la rama datos (uno por archivo, AAAA-MM-DD.tar.gz). "
                  "Los lee analisis/archivo.py completar.", "--latest=false")


def rotar(datos: Path, dias: int = 30, probar: bool = False) -> dict:
    todos = dias_de_mercado(datos)
    viejos = todos[:-dias] if len(todos) > dias else []
    res = {"quedan": len(todos) - len(viejos), "rotados": [], "errores": []}
    if probar:
        res["rotarian"] = viejos
        return res
    for d in viejos:
        tag = f"datos-{d[:7]}"
        try:
            asegurar_release(tag)
            with tempfile.TemporaryDirectory() as t:
                f = Path(t) / f"{d}.tar.gz"
                with tarfile.open(f, "w:gz") as tf:
                    tf.add(datos / d, arcname=d)
                tam = f.stat().st_size
                if f.name in assets(tag):
                    gh("release", "delete-asset", tag, f.name, "-R", REPO, "-y")
                gh("release", "upload", tag, str(f), "-R", REPO)
                if assets(tag).get(f.name) != tam:
                    raise RuntimeError("el archivo subido no tiene el tamaño esperado")
            subprocess.run(["git", "-C", str(datos), "rm", "-rq", "--", d], check=True)
            res["rotados"].append(d)
        except Exception as e:  # un día que falla se queda en datos; se reintenta la próxima vez
            res["errores"].append(f"{d}: {e}")
    return res


def completar(datos: Path) -> dict:
    datos.mkdir(parents=True, exist_ok=True)
    rels = json.loads(gh("api", f"repos/{REPO}/releases?per_page=100") or "[]")
    traidos = []
    for r in rels:
        if not r.get("tag_name", "").startswith("datos-"):
            continue
        for a in r.get("assets", []):
            d = a["name"].removesuffix(".tar.gz")
            if not DIA.match(d) or (datos / d).exists():
                continue
            with tempfile.TemporaryDirectory() as t:
                gh("release", "download", r["tag_name"], "-R", REPO, "-p", a["name"], "-D", t)
                with tarfile.open(Path(t) / a["name"]) as tf:
                    tf.extractall(datos, filter="data")
            traidos.append(d)
    return {"traidos": sorted(traidos)}


if __name__ == "__main__":
    cmd, carpeta = sys.argv[1], Path(sys.argv[2])
    if cmd == "rotar":
        n = int(sys.argv[sys.argv.index("--dias") + 1]) if "--dias" in sys.argv else 30
        print(json.dumps(rotar(carpeta, n, "--probar" in sys.argv), ensure_ascii=False))
    elif cmd == "completar":
        print(json.dumps(completar(carpeta), ensure_ascii=False))
    else:
        sys.exit("uso: archivo.py rotar|completar DIR_DATOS [--dias 30] [--probar]")
