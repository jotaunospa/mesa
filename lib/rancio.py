"""Filas con "dato rancio": no son candidatas ni entran a ninguna medición.

Hallazgo del 17-11 (explorador): en la primera foto (09:31–09:32), 44 de 112 filas tenían un cambio del
día menor a 5%, contra 5 de 215 en las fotos siguientes, en 7 de 8 días. El cambio de esas filas es
real (Nasdaq lo confirma: 49 de 52 también bajo 5%): lo rancio es la lista. El screener de Yahoo las
eligió con un % viejo (de antes de la apertura) y a la hora de la foto ya no suben 5%.

Una fila es "dato rancio" si:
1. Yahoo no da el cambio del día, o el cambio es menor a 5% (la lista la eligió con un dato viejo);
2. el cambio calculado con el precio de Nasdaq y el cierre de ayer es menor a 5%;
3. el cambio de Yahoo no calza con el de Nasdaq: diferencia mayor a 8 puntos y a 25% del cambio.
   (Entre ambas consultas pasan segundos; en acciones que suben 60% eso da varios puntos de diferencia.
   Medido del 09 al 19-11: diferencia mediana 0,5 puntos; 99% bajo 10 puntos.)
Si no hay precio de Nasdaq, solo se aplica la regla 1 (en todas las fotos, no solo la primera).
Cierre de ayer: el oficial de Yahoo (niveles, desde el 17-11); en fotos anteriores se deduce del precio
y el cambio de Yahoo, y entonces la regla 2 y la 3 comparan el precio de Yahoo con el de Nasdaq.
Determinista. No cambia vetos, semáforo ni dictamen: decide qué filas se miran.
"""
from __future__ import annotations

UMBRAL = 5.0
DIF_PUNTOS, DIF_REL = 8.0, 0.25


def cambio_nasdaq(f: dict):
    nq = (f.get("nasdaq") or {}).get("precio")
    p, c = f.get("price"), f.get("chg")
    prev = ((f.get("niveles") or {}).get("cierre_ayer") or {}).get("precio")
    if not prev and p and c is not None and c > -100:
        prev = p / (1 + c / 100)
    try:
        return (float(nq) / float(prev) - 1) * 100 if nq and prev else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def motivo(f: dict) -> str | None:
    """None si la fila es válida; si no, el motivo en palabras."""
    c = f.get("chg")
    if c is None:
        return "Yahoo no dio el cambio del día"
    if c < UMBRAL:
        return f"Yahoo la listó con un cambio viejo: a la hora de la foto va {c:+.1f}% (menos de 5%)"
    cn = cambio_nasdaq(f)
    if cn is None:
        return None
    if cn < UMBRAL:
        return f"con el precio de Nasdaq va {cn:+.1f}% (Yahoo {c:+.1f}%): menos de 5%"
    if abs(c - cn) > max(DIF_PUNTOS, DIF_REL * abs(cn)):
        return f"el cambio no calza: Yahoo {c:+.1f}%, Nasdaq {cn:+.1f}%"
    return None


def separar(filas: list[dict]) -> tuple[list[dict], list[dict]]:
    """(válidas, rancias). Las rancias van con ticker, cambios y motivo."""
    ok, ran = [], []
    for f in filas:
        m = motivo(f)
        if m is None:
            ok.append(f)
        else:
            cn = cambio_nasdaq(f)
            ran.append({"ticker": f.get("ticker"), "chg": f.get("chg"),
                        "chg_nasdaq": None if cn is None else round(cn, 2), "motivo": m})
    return ok, ran
