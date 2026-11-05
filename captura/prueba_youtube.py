"""Prueba de bloqueo: ¿puede un runner de GitHub Actions capturar una transmisión
en vivo de YouTube sin que YouTube lo frene?

No lee números ni escribe en la Mesa. Solo responde una pregunta, por tres caminos:

  A. watch  : Chrome (Playwright) abre youtube.com/watch y saca captura del video
  B. embed  : Chrome abre youtube.com/embed/<id> (reproductor embebido)
  C. ytdlp  : yt-dlp obtiene la URL del flujo y ffmpeg extrae un cuadro

Para cada camino deja un veredicto: ok / bloqueado / falla, con el motivo.
Salida: resultados/resultado.json + PNG por camino.

Uso: python prueba_youtube.py <url> [<url> ...]
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

SALIDA = Path("resultados")
SALIDA.mkdir(exist_ok=True)

# Frases con que YouTube pide verificar que no eres un bot (inglés y español).
FRASES_BOT = [
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "confirma que no eres un bot",
    "unusual traffic",
    "tráfico inusual",
    "sign in to confirm",
]


def id_de_url(url):
    m = re.search(r"(?:v=|youtu\.be/|/live/|/embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def tiene_frase_bot(texto):
    t = (texto or "").lower()
    return next((f for f in FRASES_BOT if f in t), None)


def estado_video(page):
    """Lee el <video> dos veces con 6 s de diferencia: si el tiempo avanza, está reproduciendo."""
    js = """() => {
      const v = document.querySelector('video');
      const p = document.querySelector('#movie_player, .html5-video-player');
      if (!v) return null;
      return {t: v.currentTime, ready: v.readyState, paused: v.paused,
              w: v.videoWidth, h: v.videoHeight,
              anuncio: !!(p && p.classList.contains('ad-showing'))};
    }"""
    a = page.evaluate(js)
    if a and a["paused"]:
        page.evaluate("() => { const v=document.querySelector('video'); v.muted=true; v.play().catch(()=>{}); }")
    time.sleep(6)
    b = page.evaluate(js)
    if not a or not b:
        return {"hay_video": False}
    return {"hay_video": True, "avanza": b["t"] > a["t"] + 1, "t0": a["t"], "t1": b["t"],
            "ready": b["ready"], "resolucion": f'{b["w"]}x{b["h"]}', "anuncio": b["anuncio"]}


def camino_navegador(ctx, url, nombre):
    r = {"camino": nombre, "url": url}
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        r["url_final"] = page.url
        if "consent." in page.url:  # aviso de cookies europeo: rechazar no esenciales
            for txt in ["Reject all", "Rechazar todo"]:
                b = page.get_by_role("button", name=txt)
                if b.count():
                    b.first.click()
                    break
            page.wait_for_load_state("domcontentloaded")
        time.sleep(12)
        texto = page.inner_text("body", timeout=10000)
        frase = tiene_frase_bot(texto)
        page.screenshot(path=str(SALIDA / f"{nombre}_pagina.png"))
        if frase:
            r.update(veredicto="bloqueado", motivo=f'YouTube mostró: "{frase}"')
            return r
        ev = estado_video(page)
        r["video"] = ev
        v = page.locator("video").first
        if ev.get("hay_video"):
            try:
                v.screenshot(path=str(SALIDA / f"{nombre}_video.png"), timeout=10000)
            except Exception as e:
                r["error_captura_video"] = str(e)[:200]
        if not ev.get("hay_video"):
            r.update(veredicto="falla", motivo="La página cargó pero no hay elemento de video")
        elif ev.get("anuncio"):
            r.update(veredicto="falla", motivo="Estaba corriendo un anuncio (repetir; no es bloqueo)")
        elif ev.get("avanza"):
            r.update(veredicto="ok", motivo="El video reproduce (el tiempo avanzó)")
        else:
            r.update(veredicto="falla", motivo="Hay video pero no avanza (¿transmisión no en vivo, o bloqueo silencioso?)")
        # id real si la URL era de canal (@canal/live)
        m = id_de_url(page.url) or id_de_url(page.content()[:200000])
        r["id_video"] = m
    except Exception as e:
        r.update(veredicto="falla", motivo=f"Error: {str(e)[:300]}")
    finally:
        page.close()
    return r


def camino_ytdlp(url, nombre):
    r = {"camino": nombre, "url": url}
    p = subprocess.run(["yt-dlp", "-g", "-f", "best[height<=1080]/best", "--no-warnings", url],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        err = p.stderr.strip()[-400:]
        frase = tiene_frase_bot(err)
        r.update(veredicto="bloqueado" if frase else "falla", motivo=err)
        return r
    flujo = p.stdout.strip().splitlines()[0]
    png = SALIDA / f"{nombre}_cuadro.png"
    f = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", flujo, "-frames:v", "1", str(png)],
                       capture_output=True, text=True, timeout=120)
    if f.returncode == 0 and png.exists() and png.stat().st_size > 0:
        r.update(veredicto="ok", motivo="Se extrajo un cuadro del flujo")
    else:
        r.update(veredicto="falla", motivo="yt-dlp entregó el flujo pero ffmpeg no sacó cuadro: " + f.stderr[-300:])
    return r


def main(urls):
    ip = subprocess.run(["curl", "-s", "https://api.ipify.org"], capture_output=True, text=True).stdout.strip()
    informe = {"fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "ip_runner": ip, "pruebas": []}
    with sync_playwright() as pw:
        try:
            nav = pw.chromium.launch(channel="chrome", headless=False)  # Chrome real (bajo xvfb)
            informe["navegador"] = "chrome"
        except Exception:
            nav = pw.chromium.launch(headless=False)
            informe["navegador"] = "chromium"
        ctx = nav.new_context(viewport={"width": 1600, "height": 900}, locale="en-US")
        for i, url in enumerate(urls):
            res = []
            w = camino_navegador(ctx, url, f"u{i}_watch")
            res.append(w)
            vid = w.get("id_video") or id_de_url(url)
            if vid:
                res.append(camino_navegador(ctx, f"https://www.youtube.com/embed/{vid}?autoplay=1&mute=1", f"u{i}_embed"))
            res.append(camino_ytdlp(url, f"u{i}_ytdlp"))
            informe["pruebas"].append({"url": url, "caminos": res})
        nav.close()
    (SALIDA / "resultado.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2))
    print(f"IP del runner: {ip}")
    for p in informe["pruebas"]:
        print(f"\n{p['url']}")
        for c in p["caminos"]:
            print(f"  {c['camino']:<10} {c['veredicto']:<10} {c['motivo'][:160]}")


if __name__ == "__main__":
    main(sys.argv[1:])
