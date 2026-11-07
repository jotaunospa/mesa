"""Noticias de una acción desde varias fuentes, con la misma regla de relevancia en todas.

Fuentes (gratis, sin clave):
- Google Noticias (feed RSS de búsqueda): cubre medios y servicios de comunicados
  (GlobeNewswire, PR Newswire, Business Wire, Accesswire).
- Nasdaq.com (API pública de su sitio, no oficial): noticias por ticker.
- Yahoo: la que ya trae la foto.

Una noticia cuenta como catalizador solo si es de las últimas 20 horas antes de la foto y su
titular nombra a la acción: el ticker como palabra marcada ("(RAYA)", "NASDAQ: RAYA") o la
primera palabra distintiva del nombre de la empresa. Las listas genéricas ("Most Active",
"movers", "gainers") no cuentan.

Estado de la noticia para la Mesa:
- "confirmada": alguna fuente tiene un titular válido.
- "no_encontrada": las fuentes respondieron y ninguna tiene un titular válido.
- "sin_dato": ninguna fuente respondió (no se puede afirmar que no haya noticia).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
GENERICO = re.compile(r"most active|stocks? to watch|movers|gainers|losers|stocks? moving|trending stocks|"
                      r"market (wrap|update|today)|premarket|pre-market|after[- ]hours movers|penny stocks?", re.I)
SUFIJOS = {"inc", "inc.", "corp", "corp.", "corporation", "ltd", "ltd.", "limited", "holdings", "holding", "group",
           "co", "co.", "company", "plc", "sa", "s.a.", "nv", "n.v.", "ag", "the", "technologies", "technology",
           "international", "global", "ordinary", "shares", "class", "a", "&", "acquisition", "corp,"}


# Páginas permanentes de cotización: tienen fecha reciente pero no son noticias.
PERMANENTE = re.compile(r"stock price|price today|\bquote\b|seasonal returns|\bchart\b|sec filings?\b|"
                        r"\b(call|put) \(|options? chain|technical analysis|latest stock news|news (&|and) headlines|"
                        r"stock forecast|short interest|reacting to|trading (systems?|strateg(y|ies)|plans?)\b|price action|competitors|dividend history|\bnews\s+[-\u2014]\s+(NASDAQ|NYSE)", re.I)
# Notas que solo describen el movimiento del precio ("sube 59%"): no explican el porqué.
MOVIMIENTO = re.compile(r"\b(surges?|jumps?|soars?|sinks?|falls?|plunges?|rall(y|ies)|explodes?|tumbles?|spikes?|"
                        r"rockets?|skyrockets?|drops?|climbs?|pops?|slides?|plummets?|gains?|rises?|lifted|up|down)\b[^.]{0,25}\d+(\.\d+)?%|"
                        r"\d+(\.\d+)?%\s+(jump|gain|rise|rall(y|ies)|surge|drop|fall|pop|move|spike|plunge|decline|slide|climb|pop)s?\b|"
                        r"^\s*(why|what|is|can|did|does|how)\b.{0,90}\b(stock|shares|gain|jump|rise|move|surge|rally|pop|drop|fall)|"
                        r"what'?s driving|explained by|no (company )?news|stock is (up|down|moving)|watchlist", re.I)
# Cifras de la empresa que suben ("Revenue Rises 37%") son un catalizador, no el movimiento del precio.
FUNDAMENTAL = re.compile(r"\b(revenues?|sales|earnings|net income|income|profits?|EPS|bookings|backlog|orders|margins?|ARR)\s+"
                         r"(\w+\s+){0,2}(rises?|jumps?|grows?|grew|surges?|climbs?|increases?|soars?|up)\b", re.I)
# "...jumps 45% after FDA nod": el movimiento viene con su hecho, cuenta como noticia.
CON_CAUSA = re.compile(r"\b(after|on|following)\s+(?!(\w+\s+){0,2}(peers?|sector|rivals?|market)\b)(\S+\s+){0,3}?(FDA|approval|clearance|contract|deal|agreement|award|order|LOI|"
                       r"letter of intent|partnership|collaboration|acquisition|merger|buyback|repurchase|offering|earnings|results|revenue|"
                       r"guidance|trial|data|patent|launch|listing|uplisting|split)\b", re.I)
SIN_CAUSA = re.compile(r"explained by|no (company )?news|what'?s driving|watchlist", re.I)


def es_movimiento(t: str) -> bool:
    """El titular solo describe que la acción se movió, sin un hecho de la empresa."""
    if SIN_CAUSA.search(t):
        return True
    if FUNDAMENTAL.search(t) or CON_CAUSA.search(t):
        return False
    return MOVIMIENTO.search(t) is not None


# Primeras palabras del nombre que son palabras comunes: se exige la frase de dos palabras.
COMUNES = {"mixed", "digital", "big", "global", "first", "new", "american", "united", "high", "smart", "green",
           "blue", "next", "future", "power", "energy", "data", "health", "life", "bright", "star", "golden",
           "national", "general", "capital", "world", "trend", "quantum", "solar", "clean", "safe", "pure",
           "naas", "saas", "cloud", "network", "crypto", "bitcoin"}  # también términos de la industria


def palabra_clave(nombre: str | None) -> str | None:
    """Parte distintiva del nombre ("Vivakor, Inc." -> "Vivakor"; "Mixed Martial Arts" -> "Mixed Martial")."""
    ws = [w for w in re.split(r"[\s,()]+", str(nombre or "")) if w and w.lower() not in SUFIJOS]
    if not ws or len(ws[0]) < 4:
        return None
    if ws[0].lower() in COMUNES:
        return " ".join(ws[:2]) if len(ws) > 1 else None
    return ws[0]


def nombra_a(titulo: str, tk: str, nombre: str | None) -> bool:
    """El titular habla de esta empresa (no de un homónimo ni de otra bolsa)."""
    t, e = titulo, re.escape(tk)
    if re.search(rf"\(\s*(?:NASDAQ|NYSE|NYSE American|AMEX)?\s*:?\s*{e}\s*\)", t, re.I):
        return True
    if re.search(rf"(?:NASDAQ|NYSE|AMEX)\s*:\s*{e}(?![A-Za-z.])", t, re.I) or re.search(rf"\${e}(?![A-Za-z])", t):
        return True
    w = palabra_clave(nombre)
    if not w:
        return False
    # Nombres con mayúsculas internas (NaaS) se comparan respetando mayúsculas: "Naas" es otra cosa.
    sensible = any(c.isupper() for c in w[1:]) and not w.isupper()
    return re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", t, 0 if sensible else re.I) is not None


def google(tk: str, nombre: str | None, timeout: int = 12) -> list[dict] | None:
    w = palabra_clave(nombre)
    q = f'"{tk}" stock' + (f' OR "{w}"' if w else "") + " when:2d"
    url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        items = []
        for it in ET.fromstring(r.content).iter("item"):
            titulo = (it.findtext("title") or "").strip()
            fuente = (it.findtext("source") or "").strip()
            if fuente and titulo.endswith(" - " + fuente):
                titulo = titulo[: -len(fuente) - 3]
            try:
                fe = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            items.append({"titulo": titulo, "fuente": fuente or "Google Noticias", "fecha": fe.isoformat(),
                          "url": it.findtext("link"), "origen": "google"})
        return items
    except (requests.RequestException, ET.ParseError):
        return None


def _hace(txt: str, ahora: datetime) -> datetime | None:
    m = re.match(r"(\d+)\s+(minute|hour|day)s?\s+ago", str(txt or "").strip(), re.I)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2).lower()
    return ahora - timedelta(minutes=n) if u == "minute" else ahora - timedelta(hours=n) if u == "hour" else ahora - timedelta(days=n)


def nasdaq(tk: str, timeout: int = 12, ahora: datetime | None = None) -> list[dict] | None:
    url = f"https://api.nasdaq.com/api/news/topic/articlebysymbol?q={tk.lower()}|stocks&offset=0&limit=15&fallback=false"
    try:
        r = requests.get(url, headers={**UA, "Accept": "application/json", "Origin": "https://www.nasdaq.com",
                                       "Referer": "https://www.nasdaq.com/"}, timeout=timeout)
        if r.status_code != 200:
            return None
        rows = ((r.json() or {}).get("data") or {}).get("rows") or []
        out = []
        for x in rows:
            fe = _hace(x.get("ago"), ahora or datetime.now(timezone.utc))
            exacta = fe is not None
            for k in (() if exacta else ("created", "ago", "date")):
                v = x.get(k)
                if not v:
                    continue
                for fmt in ("%b %d, %Y", "%m/%d/%Y"):
                    try:
                        fe = datetime.strptime(str(v).strip(), fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        pass
                if fe:
                    break
            out.append({"titulo": x.get("title") or "", "fuente": x.get("publisher") or "Nasdaq",
                        "fecha": fe.isoformat() if fe else None, "solo_dia": not exacta,
                        "url": "https://www.nasdaq.com" + x["url"] if str(x.get("url", "")).startswith("/") else x.get("url"),
                        "origen": "nasdaq", "crudo": {k: x.get(k) for k in ("created", "ago", "date")}})
        return out
    except (requests.RequestException, ValueError):
        return None


def evaluar(tk: str, nombre: str | None, hora: datetime, fuentes: dict[str, list | None]) -> dict:
    """Aplica la regla a todas las fuentes. `hora` es la de la foto (con zona)."""
    lim = hora - timedelta(hours=20)
    validas, respondieron = [], [k for k, v in fuentes.items() if v is not None]
    for origen, items in fuentes.items():
        for n in items or []:
            if not n.get("fecha"):
                continue
            try:
                fe = (datetime.fromtimestamp(n["fecha"], timezone.utc) if isinstance(n["fecha"], (int, float))
                      else datetime.fromisoformat(str(n["fecha"]).replace("Z", "+00:00")))
            except ValueError:
                continue
            if fe.tzinfo is None:
                fe = fe.replace(tzinfo=timezone.utc)
            if n.get("solo_dia"):  # Nasdaq a veces solo da el día: cuenta si es el mismo día de la foto
                ok_t = fe.date() == hora.astimezone(timezone.utc).date()
            else:
                ok_t = lim <= fe <= hora + timedelta(minutes=1)
            t = n.get("titulo") or ""
            if ok_t and not GENERICO.search(t) and not PERMANENTE.search(t) and not es_movimiento(t) and nombra_a(t, tk, nombre):
                validas.append({**n, "origen": origen})
    validas.sort(key=lambda n: n["fecha"], reverse=True)
    estado = "confirmada" if validas else ("no_encontrada" if respondieron else "sin_dato")
    return {"estado": estado, "validas": validas[:5], "fuentes": respondieron}
