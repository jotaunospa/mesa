"""Costos que usan todas las mediciones (en puntos porcentuales del precio).

- Spread: el de Nasdaq de la fila (bid/ask al aparecer). Ida y vuelta paga el spread completo:
  medio spread al entrar y medio al salir.
- Deslizamiento escalonado de la Mesa, por lado: 1,5% bajo 2 USD, 1% de 2 a 5 USD, 0,5% sobre 5 USD
  (mismos valores que la Mesa y analisis/backtest.py).
- Comisión de Racional: NO SE CONOCE. Se usa un SUPUESTO de 0,25% por lado hasta confirmarla.
"""

COMISION_PCT = 0.25          # SUPUESTO por lado; cambiar cuando se confirme la comisión de Racional
COMISION_ES_SUPUESTO = True


def desliz(p: float) -> float:
    return 1.5 if p < 2 else 1.0 if p < 5 else 0.5


def ida_y_vuelta(p_in: float, p_out: float, spread_pct: float | None) -> float | None:
    """Costo total de comprar a p_in y vender a p_out, en pp. None si falta el spread (no se rellena)."""
    if spread_pct is None:
        return None
    return spread_pct + desliz(p_in) + desliz(p_out) + 2 * COMISION_PCT


def nota() -> str:
    return (f"Costos netos: spread de Nasdaq de la fila (ida y vuelta) + deslizamiento escalonado de la Mesa por lado "
            f"(1,5% bajo 2 USD, 1% de 2 a 5, 0,5% sobre 5) + comisión de Racional **supuesta** en {COMISION_PCT:.2f}% por lado "
            f"(no confirmada). Sin spread en la fila, el neto queda \"sin dato\".")
