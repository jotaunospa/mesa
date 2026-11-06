"""SEC para la Mesa: dilución verificada (reglas v1.2) y filings en vivo.

Todo sale de documentos de la SEC con fecha. Lo que no se puede leer de forma
confiable se declara ausente, no se adivina.

Reglas v1.2 (ver instrucciones del proyecto Mesa):
- veto_venta:   424B* o 8-K con ítem 3.02 en los últimos 10 días
- veto_quiebra: 8-K con ítem 1.03 (cualquier fecha en 12 meses)
- dilución alta:  venta (424B / 8-K 3.02) en 90 días, o registro S-1/S-3/F-1/F-3
                  en 12 meses + going concern
- dilución media: registro en 12 meses, o going concern
- dilución baja:  nada de lo anterior

Going concern no se puede leer sin parsear el texto del 10-K/10-Q. Mientras no
esté, se usa como sustituto "autonomía de caja < 4 trimestres" (de Faro), y se
dice en `dil_motivo` que es un sustituto. Emisores extranjeros (20-F/6-K):
"información limitada", porque no presentan 8-K.
"""
from __future__ import annotations

import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from lib.datos_sec import SEC, fundamentales

UA = "Mesa/0.1 (contacto-en-secreto-SEC_CONTACTO)"
REGISTROS = {"S-1", "S-3", "F-1", "F-3", "S-1/A", "S-3/A", "F-1/A", "F-3/A", "S-3ASR", "F-3ASR"}

_cliente: Optional[SEC] = None


def cliente() -> SEC:
    global _cliente
    if _cliente is None:
        _cliente = SEC(user_agent=UA)
    return _cliente


def _dias(fecha: str, hoy: date) -> int:
    return (hoy - date.fromisoformat(fecha)).days


def dilucion(ticker: str, hoy: Optional[date] = None, splits_12m=None) -> Dict[str, Any]:
    """Nivel de dilución y vetos desde /submissions + autonomía de caja de Faro."""
    hoy = hoy or datetime.now(timezone.utc).date()
    c = cliente()
    out: Dict[str, Any] = {"dil": None, "dil_motivo": [], "veto_venta": False, "veto_quiebra": False,
                           "filings_recientes": [], "extranjero": False, "runway_q": None,
                           "caja_periodo": None, "estado": "ok"}
    cik = c.cik(ticker)
    if not cik:
        out.update(estado="sin_cik", dil_motivo=["no declara a la SEC (o ticker no resuelto)"])
        return out
    st, data, motivo = c._get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", "data.sec.gov")
    if st != 200 or not data:
        out.update(estado=f"sin_submissions: {motivo}")
        return out
    rec = (data.get("filings") or {}).get("recent") or {}
    forms, fechas, items = rec.get("form", []), rec.get("filingDate", []), rec.get("items", [])
    venta_10 = venta_90 = registro_12m = quiebra = False
    for i, (f, d) in enumerate(zip(forms, fechas)):
        dd = _dias(d, hoy)
        if dd > 365:
            break
        it = items[i] if i < len(items) else ""
        es_venta = f.startswith("424B") or (f.startswith("8-K") and "3.02" in (it or ""))
        if es_venta and dd <= 10:
            venta_10 = True
        if es_venta and dd <= 90:
            venta_90 = True
        if f in REGISTROS:
            registro_12m = True
        if f.startswith("8-K") and "1.03" in (it or ""):
            quiebra = True
        if f in ("20-F", "6-K", "40-F"):
            out["extranjero"] = True
        if dd <= 30 and (es_venta or f in REGISTROS or f.startswith("8-K")):
            out["filings_recientes"].append({"form": f, "fecha": d, "items": it})

    # Acciones en circulación de la portada más reciente (10-Q/10-K/20-F), para
    # contrastar el float de Yahoo: el float nunca puede ser mayor que esto.
    out["acciones_portada"] = None
    try:
        data_f, _ = c.hechos(cik)
        ent = ((((data_f or {}).get("facts") or {}).get("dei") or {})
               .get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares", [])
        if ent:
            e = max(ent, key=lambda x: (x.get("end", ""), x.get("filed", "")))
            out["acciones_portada"] = {"valor": e["val"], "fecha": e["end"], "form": e.get("form")}
            out["ventas_tras_portada"] = sum(1 for f, d in zip(forms, fechas)
                                             if d > e["end"] and (f.startswith("424B")))
    except Exception:
        pass

    # Autonomía de caja (Faro). Ojo: Faro ancla en el último ejercicio ANUAL,
    # así que la caja puede tener hasta ~12 meses. Se informa la fecha del dato.
    try:
        fu = fundamentales(ticker, cliente=c)
        if fu:
            caja, fcf = fu.val("caja"), fu.fcf()
            out["caja_periodo"] = fu.periodo
            if caja is not None and fcf is not None and fcf < 0:
                out["runway_q"] = round(caja / (-fcf / 4.0), 1)
    except Exception as e:  # la caja es un extra; no tumba la fila
        out["dil_motivo"].append(f"caja no disponible ({type(e).__name__})")

    # Dilución v2: 7 factores
    try:
        data_f, _ = c.hechos(cik)
    except Exception:
        data_f = None
    acc = (out.get("acciones_portada") or {}).get("valor")
    fx7 = factores_dilucion(data_f, forms, fechas, items, hoy, acc, splits_12m)
    out["dil7"] = fx7
    if fx7["detalle"].get("runway_q") is not None:
        out["runway_q"], out["caja_periodo"] = fx7["detalle"]["runway_q"], fx7["detalle"].get("caja_al")
    corto = out["runway_q"] is not None and out["runway_q"] < 4
    out["veto_venta"], out["veto_quiebra"] = venta_10, quiebra
    # Nivel v2 (06-11), calibrado contra el scanner en 23 acciones (19/23; la regla v1.2 daba 15/23):
    #   alta  = caja corta (< 4 trimestres) y al menos 2 de los 7 factores
    #   media = al menos 2 factores, o caja corta
    #   baja  = el resto
    # Los vetos (venta en 10 días, quiebra) no cambian.
    p7 = fx7["puntaje"]
    caja_corta = fx7["factores"].get("caja_corta") is True
    if caja_corta and p7 >= 2:
        out["dil"] = "alta"
    elif p7 >= 2 or caja_corta:
        out["dil"] = "media"
    else:
        out["dil"] = "baja"
    out["dil_puntaje"] = f"{p7}/{fx7['cobertura']}"
    nombres = {"caja_corta": "caja para menos de 4 trimestres", "shelf_activo": "shelf S-3/F-3 vigente",
               "venta_reciente": "venta de acciones en 6 meses", "warrants": "warrants ≥ 20% de las acciones",
               "convertibles": "deuda convertible", "aviso_bolsa": "aviso de la bolsa (8-K 3.01)",
               "split_inverso": "split inverso en 12 meses"}
    out["dil_factores_si"] = [nombres[k] for k, v in fx7["factores"].items() if v]
    if venta_10: out["dil_motivo"].append("venta de acciones (424B / 8-K 3.02) en 10 días → VETO")
    elif venta_90: out["dil_motivo"].append("venta de acciones en 90 días")
    if registro_12m: out["dil_motivo"].append("registro S-1/S-3/F-1/F-3 en 12 meses")
    if corto: out["dil_motivo"].append(f"autonomía de caja {out['runway_q']} trimestres "
                                       f"(sustituto de going concern; dato al {out['caja_periodo']})")
    if quiebra: out["dil_motivo"].append("8-K 1.03 (quiebra) → VETO")
    if out["extranjero"]:
        out["dil_motivo"].append("emisor extranjero: información limitada (no presenta 8-K)")
    return out


def filings_en_vivo(tipos=("424B", "8-K", "S-1", "S-3", "F-1", "F-3", "6-K")) -> Dict[str, List[dict]]:
    """Feed 'current events' de EDGAR: lo que entró hoy, por ticker.

    Devuelve {TICKER: [{form, hora, titulo}]}. Solo los de hoy (hora de NY no
    importa: el feed trae los últimos ~100 por tipo).
    """
    c = cliente()
    cik_a_tk: Dict[str, str] = {}
    for tk, ck in c.mapa_tickers().items():
        cik_a_tk.setdefault(str(int(ck)), tk)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    from zoneinfo import ZoneInfo
    hoy = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    out: Dict[str, List[dict]] = {}
    for t in tipos:
        url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type="
               f"{t}&company=&dateb=&owner=include&count=100&output=atom")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raiz = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            continue
        for e in raiz.findall("a:entry", ns):
            titulo = e.findtext("a:title", default="", namespaces=ns)
            upd = e.findtext("a:updated", default="", namespaces=ns)
            m = re.search(r"\((\d{6,10})\)", titulo)
            if not m or not upd.startswith(hoy):
                continue
            tk = cik_a_tk.get(str(int(m.group(1))))
            if not tk:
                continue
            form = titulo.split(" - ")[0].strip()
            out.setdefault(tk, []).append({"form": form, "hora": upd, "titulo": titulo[:140]})
        time.sleep(0.2)
    return out

# ───────────── dilución de 7 factores (v2, 06-11) ─────────────
# El scanner del canal puntúa la dilución con 7 factores ("HIGH 7/7": nivel + cuántos factores
# tuvieron dato). Estos son los nuestros, todos leídos de la SEC con fecha:
FACTORES = ["caja_corta", "shelf_activo", "venta_reciente", "warrants", "convertibles",
            "aviso_bolsa", "split_inverso"]


def _ultimo(facts: dict, conceptos, unidad="USD", taxo="us-gaap"):
    """Último valor reportado (por fecha de cierre) entre varios conceptos XBRL."""
    mejor = None
    for c in conceptos:
        for e in (((facts.get(taxo) or {}).get(c) or {}).get("units") or {}).get(unidad, []):
            if mejor is None or (e.get("end", ""), e.get("filed", "")) > (mejor.get("end", ""), mejor.get("filed", "")):
                mejor = dict(e, concepto=c)
    return mejor


def factores_dilucion(data_f, forms, fechas, items, hoy, acciones, splits_12m=None) -> dict:
    f = (data_f or {}).get("facts") or {}
    out = {k: None for k in FACTORES}
    det = {}
    # 1) caja corta: caja del último período / quema trimestral (flujo operativo, del último 10-Q o 10-K)
    caja = _ultimo(f, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"])
    ocf = None
    for e in sorted((((f.get("us-gaap") or {}).get("NetCashProvidedByUsedInOperatingActivities") or {}).get("units") or {}).get("USD", []),
                    key=lambda x: (x.get("end", ""), x.get("filed", "")), reverse=True):
        if e.get("start") and e.get("form", "").startswith(("10-Q", "10-K", "20-F")):
            ocf = e; break
    if caja and ocf:
        meses = max(1, round((date.fromisoformat(ocf["end"]) - date.fromisoformat(ocf["start"])).days / 30.4))
        quema_t = -ocf["val"] / (meses / 3)
        if quema_t > 0:
            runway = caja["val"] / quema_t
            out["caja_corta"] = runway < 4
            det["runway_q"] = round(runway, 1)
        else:
            out["caja_corta"] = False
            det["runway_q"] = None
        det["caja_al"] = caja["end"]
    # 2..6) desde /submissions (hasta 3 años para el shelf)
    shelf = venta = aviso = False
    for i, (fo, d) in enumerate(zip(forms, fechas)):
        dd = (hoy - date.fromisoformat(d)).days
        if dd > 3 * 365:
            break
        it = items[i] if i < len(items) else ""
        if fo in ("S-3", "F-3", "S-3ASR", "F-3ASR", "S-3/A", "F-3/A"):
            shelf = True
        if dd <= 180 and (fo.startswith("424B") or (fo.startswith("8-K") and "3.02" in (it or ""))):
            venta = True
        if dd <= 365 and fo.startswith("8-K") and "3.01" in (it or ""):
            aviso = True
    out["shelf_activo"], out["venta_reciente"], out["aviso_bolsa"] = shelf, venta, aviso
    # 4) warrants vigentes vs acciones en circulación
    w = _ultimo(f, ["ClassOfWarrantOrRightOutstanding"], unidad="shares")
    if w and acciones:
        if (hoy - date.fromisoformat(w["end"])).days <= 450:
            out["warrants"] = w["val"] / acciones >= 0.2
            det["warrants_pct"] = round(w["val"] / acciones * 100, 1)
        else:
            out["warrants"] = False
    elif f:
        out["warrants"] = False
    # 5) convertibles en el balance (último año)
    cv = _ultimo(f, ["ConvertibleNotesPayable", "ConvertibleNotesPayableCurrent", "ConvertibleDebtNoncurrent",
                     "ConvertibleDebtCurrent", "ConvertibleDebt"])
    if f:
        out["convertibles"] = bool(cv and cv["val"] > 0 and (hoy - date.fromisoformat(cv["end"])).days <= 450)
    # 7) split inverso en 12 meses (las que lo hacen suelen emitir después)
    if splits_12m is not None:
        out["split_inverso"] = any(x.get("ratio", 1) < 1 for x in splits_12m)
    con_dato = [k for k in FACTORES if out[k] is not None]
    puntaje = sum(1 for k in con_dato if out[k])
    return {"factores": out, "puntaje": puntaje, "cobertura": len(con_dato), "detalle": det}
