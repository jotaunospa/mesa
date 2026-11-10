"""Subir commits a la rama `datos` sin quedar trabado.

Varios procesos escriben la rama a la vez (Escáner, grandes, premarket, cierre, publicar-web).
Si un `pull --rebase` choca, el clon quedaba a medio rebase y ninguna foto siguiente subía.
Aquí: se aborta cualquier rebase pendiente, se rebasa dando prioridad a lo propio (-X theirs:
en un rebase "theirs" son los commits locales) y se reintenta.
"""
from __future__ import annotations

import subprocess
import time


def empujar(g: list[str], intentos: int = 5) -> bool:
    for i in range(intentos):
        if subprocess.run(g + ["push", "-q", "origin", "HEAD:datos"]).returncode == 0:
            return True
        subprocess.run(g + ["rebase", "--abort"], capture_output=True)
        r = subprocess.run(g + ["pull", "-q", "--rebase", "-X", "theirs", "origin", "datos"])
        if r.returncode != 0:
            subprocess.run(g + ["rebase", "--abort"], capture_output=True)
            print("[datos] el rebase falló; reintento", flush=True)
        time.sleep(3 + 2 * i)
    print("[datos] no pude subir a la rama datos", flush=True)
    return False
