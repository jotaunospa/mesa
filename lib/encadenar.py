"""Encadenar procesos dentro de GitHub Actions (sin depender de tareas externas).

Un workflow puede lanzar otro con el GITHUB_TOKEN del propio job (workflow_dispatch es la
excepción que GitHub permite). Así: premarket → ventana → (continuación) → after-hours → cierre.
Fuera de GitHub (sin token o sin GITHUB_REPOSITORY) no hace nada.
"""
from __future__ import annotations

import json
import os
import urllib.request


def lanzar(workflow: str, **inputs) -> bool:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(f"[encadenar] sin token/repositorio: no lanzo {workflow}")
        return False
    body = json.dumps({"ref": os.environ.get("GITHUB_REF_NAME") or "main",
                       "inputs": {k: str(v) for k, v in inputs.items()}}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                                 data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    for _ in range(3):
        try:
            urllib.request.urlopen(req, timeout=20)
            print(f"[encadenar] lancé {workflow} {inputs}")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[encadenar] falló {workflow}: {e}")
    return False
