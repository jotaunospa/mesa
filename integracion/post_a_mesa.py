"""After-hours → Mesa, sección "Para vigilar mañana" (sin dictamen: solo lista para mirar en el premarket).

Lo usa la tarea programada "Escáner Mesa: after-hours a la Mesa" (~20:07 NY). Lee la última foto del
after-hours en la rama `datos` (gh api) y deja el lote de escrituras para ArtifactData "batch"
(colección data/users/me). La Mesa muestra en "Para vigilar mañana" las lecturas de sesión post de
las últimas 20 h con cambio ≥ 10% o con noticia; aquí se escriben solo esas (hasta 30).

Uso: python integracion/post_a_mesa.py --out DIR [--dia AAAA-MM-DD]
Imprime una línea JSON: {"ok": true, "foto": "1947", "filas": 12, "lote": "DIR/lote.json"} o {"ok": false, "motivo": ...}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.a_mesa import _categoria  # misma clasificación de noticias que el Escáner

REPO = "jotaunospa/mesa"
NY = ZoneInfo("America/New_York")
CAMBIO_MIN, N_MAX = 10, 30


def raw(path: str):
    r = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{path}?ref=datos",
                        "-H", "Accept: application/vnd.github.raw"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def s(v) -> str:
    return "" if v is None else str(v)


def lecturas(snap: dict) -> list[dict]:
    ahora = datetime.fromisoformat(snap["hora_ny"])
    fecha, hhmm, foto = ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"), ahora.strftime("%H%M")
    ts = ahora.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    batch = f"b_esc_post_{fecha.replace('-', '')}_{foto}"
    out = []
    for f in snap.get("filas", []):
        if "error" in f or f.get("pm_cambio_tv") is None:
            continue
        ev = f.get("noticias_eval") or {}
        hl = str((ev.get("validas") or [{}])[0].get("titulo") or "") if ev.get("validas") else ""
        nw = _categoria(hl) if hl else "ninguna"
        if f["pm_cambio_tv"] < CAMBIO_MIN and nw == "ninguna":
            continue
        nq = f.get("nasdaq") or {}
        vol = nq.get("vol") or f.get("pm_vol_tv")
        out.append({
            "id": f"r_esc_post_{fecha.replace('-', '')}_{foto}_{f['ticker']}", "kind": "read", "ts": ts,
            "etDate": fecha, "etTime": hhmm, "ticker": f["ticker"], "price": s(f.get("pm_precio_tv")),
            "chg": s(f.get("pm_cambio_tv")), "volM": s(None if vol is None else round(vol / 1e6, 3)), "rvol": "",
            "floatM": s(f.get("floatM_tv")), "dayLow": s(nq.get("min") or f.get("pm_min_tv")),
            "dayHigh": s(nq.get("max") or f.get("pm_max_tv")), "dil": "medio", "vwap": "na",
            "news": nw, "headline": hl[:90], "halt": "na", "stC": "", "stM": "", "casi": "", "rulesV": "",
            "sess": "post", "src": "escaner", "batch": batch,
            "esc": {"apto": None, "motivos": [], "cautela": ["after-hours: sin dictamen"], "foto": hhmm,
                    "spread": None, "url": "https://mesa-liart.vercel.app"},
        })
    out.sort(key=lambda r: -float(r["chg"]))
    return out[:N_MAX]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dia", default=datetime.now(NY).strftime("%Y-%m-%d"))
    a = ap.parse_args()
    snap = raw(f"{a.dia}/postmarket/ultimo.json")
    if not snap:
        print(json.dumps({"ok": False, "motivo": f"sin fotos de after-hours el {a.dia}"})); return
    reads = lecturas(snap)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.json"):
        p.unlink()
    writes = []
    for r in reads:
        f = out / f"{r['id']}.json"
        f.write_text(json.dumps(r, ensure_ascii=False))
        writes.append({"op": "set", "collection": "data/users/me", "doc_id": r["id"], "file_path": str(f)})
    (out / "lote.json").write_text(json.dumps(writes, ensure_ascii=False, indent=1))
    print(json.dumps({"ok": True, "foto": snap["hora_ny"][11:16], "filas_foto": len(snap.get("filas", [])),
                      "filas": len(reads), "lote": str(out / "lote.json")}))


if __name__ == "__main__":
    main()
