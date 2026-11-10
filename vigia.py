"""Vigía: revisa cada ~15 min (cron de GitHub) que esté corriendo lo que corresponde a la hora, y si falta, lo lanza.

No depende de Claude ni de tareas externas. Si el cron de GitHub llega tarde, la próxima pasada corrige;
y como cada etapa lanza a la siguiente (lib/encadenar.py), basta con que una pasada del vigía llegue al día.

Horario (NY, días hábiles sin feriados de NYSE):
- 06:40–09:20  premarket   (premarket.yml, sesion=pre)
- 09:25–15:50  Escáner     (ventana.yml)
- 16:05–19:45  after-hours (premarket.yml, sesion=post)
- 16:45–23:59  cierre      (cierre.yml) si todavía no existe resultados.json del día
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lib.encadenar import lanzar

NY = ZoneInfo("America/New_York")
REPO = os.environ.get("GITHUB_REPOSITORY", "jotaunospa/mesa")
FERIADOS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}


def api(path: str):
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/{path}", headers=h),
                                                 timeout=20).read())
    except Exception as e:  # noqa: BLE001
        print(f"api {path}: {e}")
        return None


def activo(workflow: str) -> bool:
    """¿Hay una ejecución de ese workflow en curso o en cola, creada en las últimas 7 h?"""
    desde = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for st in ("in_progress", "queued", "waiting", "pending"):
        r = api(f"repos/{REPO}/actions/workflows/{workflow}/runs?status={st}&created=>={desde}&per_page=5")
        if r and r.get("total_count"):
            return True
    return False


def existe(path: str) -> bool:
    return api(f"repos/{REPO}/contents/{path}?ref=datos") is not None


def main() -> None:
    a = datetime.now(NY)
    hoy, m = a.strftime("%Y-%m-%d"), a.hour * 60 + a.minute
    print(f"vigía {a:%a %Y-%m-%d %H:%M} NY")
    if os.environ.get("PROBAR_LANZAR"):  # prueba manual: confirma que el token puede lanzar workflows
        print("prueba:", lanzar("diag.yml")); return
    if a.weekday() >= 5 or hoy in FERIADOS:
        print("bolsa cerrada hoy"); return
    if 6 * 60 + 40 <= m <= 9 * 60 + 20 and not activo("premarket.yml"):
        lanzar("premarket.yml", modo="ventana", sesion="pre")
    if 9 * 60 + 25 <= m <= 15 * 60 + 55 and not activo("ventana.yml"):
        lanzar("ventana.yml", modo="ventana")
    if 16 * 60 + 5 <= m <= 19 * 60 + 45 and not activo("premarket.yml"):
        lanzar("premarket.yml", modo="ventana", sesion="post")
    if m >= 16 * 60 + 45 and not existe(f"{hoy}/resultados.json") and not activo("cierre.yml"):
        lanzar("cierre.yml")
    print("listo")


if __name__ == "__main__":
    main()
