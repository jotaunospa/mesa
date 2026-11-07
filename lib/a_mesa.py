"""Traduce una foto del Escáner al formato de filas de la Mesa de Papel.

Determinista: misma foto, mismas filas. Escribe AAAA-MM-DD/mesa.json, que una tarea
programada copia a la base de datos de la Mesa (documento `bench` + una lectura por fila).

Reglas de traducción (todas visibles para José en la Mesa):
- Números: tal cual el Escáner. RVOL = rvol por hora del día si existe; si no, el del día.
- Float: el que usa el Escáner; si está en disputa o es de una sola fuente queda marcado "?".
- Dilución: alta/media/baja -> alto/medio/bajo.
- VWAP: sobre o bajo según el precio contra el VWAP calculado del día.
- Pausa: "si" si está pausada o se reanudó hace menos de 15 min (la misma regla del dictamen).
- Noticia: solo cuentan titulares de las últimas 20 horas; se clasifican por palabras clave
  con las mismas categorías de la Mesa. Es aproximado, por eso va marcada "?".
- Dictamen del Escáner (`esc`): si dice NO APTA, la Mesa lo trata como veto (NO-GO).
- Gráfico: últimas 40 velas de 1 minuto de la sesión regular con su VWAP, para que la Mesa
  busque el setup sola. Son velas reales de Yahoo, no transcritas.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

URL = "https://mesa-liart.vercel.app"
VER = 1

# Categorías de noticia de la Mesa. Orden = prioridad (la primera que calza gana).
_REGLAS = [
    ("quiebra", r"chapter 11|bankrupt|bancarrota|quiebra|liquidat|receivership|delist|deslist"),
    ("oferta", r"offering|registered direct|private placement|at[- ]the[- ]market|\bATM\b|warrant inducement|"
               r"share sale|stock sale|sells? (\$?[\d.]+ ?(million|M) (of|in) )?(common )?(shares|stock)|convertible (preferred|notes?|debentures?)|"
               r"prices? .{0,40}(public|direct) offering|shelf registration|S-1 |S-3 |F-1 |F-3 |colocaci[oó]n"),
    ("adquisicion", r"(acquire|acquisition of|to be acquired|buyout|take[- ]private|tender offer).{0,80}"
                    r"(\$\s?\d+(\.\d+)?\s*(per|a|/)\s*share|per share in cash)"),
    ("fusion", r"merger|merge|business combination|fusi[oó]n|acquisition|acquire"),
    ("clinico", r"\bFDA\b|phase [1-3i]|clinical|trial|topline|PDUFA|IND |BLA|NDA|orphan drug|breakthrough therapy"),
    ("resultados", r"earnings|quarter(ly)? results|revenue|Q[1-4] (20\d\d )?(results|financial)|guidance|EPS"),
    ("contrato", r"contract|agreement|partner|collaborat|order|deal|award|alianza|contrato|LOI|letter of intent"),
]


def _s(v) -> str:
    return "" if v is None else str(v)


_GENERICO = re.compile(r"most active|stocks? to watch|movers|gainers|losers|stocks? moving|trending stocks|"
                       r"market (wrap|update|today)|premarket|pre-market|after[- ]hours movers", re.I)
_SUFIJOS = {"inc", "inc.", "corp", "corp.", "corporation", "ltd", "ltd.", "limited", "holdings", "holding", "group",
            "co", "co.", "company", "plc", "sa", "s.a.", "nv", "n.v.", "ag", "the", "technologies", "technology",
            "international", "global", "ordinary", "shares", "class", "a", "&"}


def _habla_de(t: str, tk: str, nombre: str | None) -> bool:
    """El titular nombra al ticker o a la empresa (Yahoo mezcla notas de otras acciones)."""
    if re.search(rf"(?<![A-Z]){re.escape(tk)}(?![A-Z])", t):
        return True
    pal = [w for w in re.split(r"[\s,()]+", str(nombre or "")) if w and w.lower() not in _SUFIJOS and len(w) > 2]
    return bool(pal) and pal[0].lower() in t.lower()


def _noticia(f: dict, ahora: datetime) -> tuple[str, str, bool]:
    """Si la foto trae la evaluación de las tres fuentes (lib/noticias.py), manda esa."""
    ev = f.get("noticias_eval")
    if ev:
        if ev.get("validas"):
            t = str(ev["validas"][0].get("titulo") or "")
            return _categoria(t), t, True
        return "ninguna", "", ev.get("estado") == "sin_dato"
    return _noticia_yahoo(f, ahora)


def _categoria(t: str) -> str:
    for cat, rx in _REGLAS:
        if re.search(rx, t, re.I):
            if cat in ("adquisicion", "fusion") and re.search(r"terminat|cancel|rescind|abandon|withdraw", t, re.I):
                return "otra"
            return cat
    return "otra"


def _noticia_yahoo(f: dict, ahora: datetime) -> tuple[str, str, bool]:
    """(categoría, titular, dudosa). Solo titulares de las últimas 20 horas, propios de la acción."""
    lim = ahora - timedelta(hours=20)
    nombre = (f.get("meta") or {}).get("nombre")
    recientes = []
    for n in f.get("noticias") or []:
        try:
            fe = datetime.fromisoformat(str(n.get("fecha", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        t = str(n.get("titulo") or "")
        if fe >= lim and not _GENERICO.search(t) and _habla_de(t, f["ticker"], nombre):
            recientes.append(n)
    if not recientes:
        return "ninguna", "", False
    t = str(recientes[0].get("titulo") or "")
    for cat, rx in _REGLAS:
        if re.search(rx, t, re.I):
            # Cancelar una fusión o compra no es una compra a precio fijo.
            if cat in ("adquisicion", "fusion") and re.search(r"terminat|cancel|rescind|abandon|withdraw", t, re.I):
                return "otra", t, True
            return cat, t, True
    return "otra", t, True


def _pausa(f: dict, ahora: datetime) -> str:
    if f.get("halt"):
        return "si"
    for h in f.get("halts_hoy") or []:
        r = str(h.get("reanuda") or "")[:5]
        if not r:
            return "si"  # pausada sin hora de reanudación
        try:
            rh, rm = int(r[:2]), int(r[3:5])
        except ValueError:
            continue
        mins = ahora.hour * 60 + ahora.minute - (rh * 60 + rm)
        if 0 <= mins < 15:  # misma regla que el dictamen del Escáner
            return "si"
    return "no"


def _grafico(velas: list | None, hasta: str = "16:00") -> dict | None:
    """Velas regulares (09:30 hasta la hora de la foto) con VWAP acumulado; las últimas 40."""
    if not velas:
        return None
    reg = [v for v in velas if "09:30" <= str(v[0]) < min(hasta, "16:00")]
    if len(reg) < 8:
        return None
    pv = vv = 0.0
    out = []
    for t, o, h, l, c, vol in reg:
        tp = (h + l + c) / 3
        pv += tp * vol
        vv += vol
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "vw": round(pv / vv, 4) if vv else None})
    return {"candles": out[-40:], "tf": "1m", "at": "", "vwApx": False, "vwNow": out[-1]["vw"], "src": "escaner"}


def fila_mesa(f: dict, ahora: datetime, velas: list | None) -> dict:
    nw, hl, nw_duda = _noticia(f, ahora)
    unsure = [u for u in (f.get("unsure") or []) if u in ("price", "chg", "volM", "rvol", "floatM", "dayLow", "dayHigh")]
    fx = f.get("float_cruce") or {}
    if fx.get("estado") in ("en_disputa", "una_fuente", "sin_dato") and "floatM" not in unsure:
        unsure.append("floatM")
    flo = f.get("floatM")
    if flo is None and fx.get("rango_M"):
        flo = fx["rango_M"][0]
    rv = f.get("rvol_hora") if f.get("rvol_hora") is not None else f.get("rvol")
    if f.get("rvol_hora") is None and "rvol" not in unsure:
        unsure.append("rvol")
    if nw_duda:
        unsure.append("news")
    sv = f.get("sobre_vwap_pct")
    row = {
        "ticker": f["ticker"], "price": _s(f.get("price")), "chg": _s(f.get("chg")), "volM": _s(f.get("volM")),
        "rvol": _s(None if rv is None else round(rv, 1)), "floatM": _s(flo),
        "floatMinM": _s((fx.get("rango_M") or [None, None])[0]), "floatMaxM": _s((fx.get("rango_M") or [None, None])[1]),
        "dayLow": _s(f.get("dayLow")), "dayHigh": _s(f.get("dayHigh")),
        "dil": {"alta": "alto", "media": "medio", "baja": "bajo"}.get(f.get("dil"), "medio"),
        "vwap": "na" if sv is None else ("sobre" if sv >= 0 else "bajo"),
        "news": nw, "deal": "", "headline": hl[:90], "halt": _pausa(f, ahora),
        "unsure": unsure, "confirm": False,
        "esc": {"apto": None if f.get("apto") is None else bool(f.get("apto")), "motivos": f.get("no_apto_por") or [], "cautela": f.get("cautela") or [],
                "foto": ahora.strftime("%H:%M"), "spread": f.get("spread_pct"), "url": URL},
    }
    g = _grafico(velas, ahora.strftime("%H:%M"))
    if g:
        row["chart"] = g
    return row


def _sess(hhmm: str) -> str:
    m = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    return "noche" if m < 240 else "pre" if m < 570 else "ventana" if m < 630 else "dia" if m < 960 else "post" if m < 1200 else "noche"


def construir(snap: dict, velas: dict) -> dict:
    ahora = datetime.fromisoformat(snap["hora_ny"])
    fecha, hhmm = ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M")
    foto = ahora.strftime("%H%M")
    batch = f"b_esc_{fecha.replace('-', '')}_{foto}"
    ts = ahora.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # mismo formato que la Mesa
    filas = [fila_mesa(f, ahora, velas.get(f["ticker"])) for f in snap.get("filas", [])]
    reads = []
    for x in filas:
        r = {k: v for k, v in x.items() if k not in ("unsure", "confirm", "chart", "deal")}
        r.update({"id": f"r_esc_{fecha.replace('-', '')}_{foto}_{x['ticker']}", "kind": "read", "ts": ts,
                  "etDate": fecha, "etTime": hhmm, "stC": "", "stM": "", "casi": "", "rulesV": "",
                  "sess": _sess(hhmm), "src": "escaner", "batch": batch})
        if x.get("chart"):
            r["chart"] = {"candles": x["chart"]["candles"], "tf": "1m"}
        x["readId"] = r["id"]
        reads.append(r)
    bench = {"kind": "bench", "ts": ts, "ex": False, "sel": 0, "rows": filas,
             "meta": {"src": "escaner", "time": f"{hhmm} NY", "foto": f"{fecha} {foto}", "url": URL,
                      "temperatura": (snap.get("temperatura") or {}).get("nivel")}}
    return {"version": VER, "hora_ny": snap["hora_ny"], "foto": foto, "bench": bench, "reads": reads}
