"""Ruido social de StockTwits (solo registro y medición; no entra a ninguna regla).

Fuente: flujo público por acción, sin clave: https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json
(la idea viene de TradingAgents, Apache 2.0: https://github.com/TauricResearch/TradingAgents,
commit 9fa1196, tradingagents/dataflows/vendors/stocktwits.py; el código es propio).

El flujo trae los últimos 30 mensajes. Con eso se mide:
- mensajes en la última hora;
- ritmo normal: mensajes por hora en todo el tramo que cubren esos 30 (desde el más viejo hasta ahora);
- cambio = mensajes de la última hora / ritmo normal. Si los 30 caen dentro de la última hora, el flujo
  está "saturado": hay 30 o más, y el cambio real no se puede saber (se dice).
- etiquetas que pusieron los autores (alcista / bajista), contadas tal cual. No dicen nada de calidad.

Límites: StockTwits limita las consultas sin clave (unas 200 por hora). Se guarda cada acción 15 min y
no se hacen más de 150 consultas por hora; pasado eso, "sin dato" con el motivo.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

CACHE_S, TOPE_HORA = 900, 150
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json"}
_cache: dict = {}
_llamadas: list[float] = []


def medir(tk: str) -> dict:
    ahora = time.time()
    hit = _cache.get(tk)
    if hit and ahora - hit[0] < CACHE_S:
        return hit[1]
    while _llamadas and ahora - _llamadas[0] > 3600:
        _llamadas.pop(0)
    if len(_llamadas) >= TOPE_HORA:
        return {"fuente": "StockTwits", "error": "tope de 150 consultas por hora alcanzado"}
    _llamadas.append(ahora)
    try:
        req = urllib.request.Request(f"https://api.stocktwits.com/api/2/streams/symbol/{tk.upper()}.json", headers=UA)
        js = json.loads(urllib.request.urlopen(req, timeout=5).read())
        msgs = js.get("messages") or []
        t = [datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")).timestamp() for m in msgs if m.get("created_at")]
        if not t:
            out = {"fuente": "StockTwits", "mensajes_1h": 0, "muestra": 0, "nota": "sin mensajes en el flujo"}
        else:
            ult_h = sum(1 for x in t if ahora - x <= 3600)
            horas = max((ahora - min(t)) / 3600, 1 / 60)
            ritmo = len(t) / horas
            sat = len(t) >= 30 and ult_h == len(t)
            sent = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in msgs]
            out = {"fuente": "StockTwits", "mensajes_1h": ult_h, "muestra": len(t),
                   "ritmo_normal_h": round(ritmo, 2), "horas_cubiertas": round(horas, 1),
                   "cambio": None if sat else round(ult_h / ritmo, 2), "saturado": sat,
                   "alcistas": sent.count("Bullish"), "bajistas": sent.count("Bearish")}
        out["hora_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception as e:
        out = {"fuente": "StockTwits", "error": f"{type(e).__name__}: {e}"[:160]}
    _cache[tk] = (ahora, out)
    return out
