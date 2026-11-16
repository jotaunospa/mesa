"""Medias móviles exponenciales (EMA) sobre velas de 1 min, incluyendo el premarket.

Definición única para la Mesa, el Escáner (misma fórmula en web/index.html) y analisis/variantes.py:
- alfa = 2 / (n + 1);
- la primera EMA es el promedio simple de los primeros n cierres; antes de eso no hay valor (None);
- cada vela con operaciones cuenta como un período. El premarket tiene minutos sin operaciones:
  esos minutos no existen como vela, así que "20 períodos" puede abarcar más de 20 minutos.
Solo visual y medición: no entra a ninguna regla de entrada.
"""
from __future__ import annotations


def ema(cierres: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(cierres)
    if len(cierres) < n:
        return out
    e = sum(cierres[:n]) / n
    out[n - 1] = e
    k = 2 / (n + 1)
    for i in range(n, len(cierres)):
        e = cierres[i] * k + e * (1 - k)
        out[i] = e
    return out


def motivo_falta(n_velas: int, n: int) -> str:
    return (f"EMA {n} no se dibuja: hay {n_velas} velas de 1 min con operaciones antes de esta hora "
            f"(incluido el premarket) y se necesitan {n}")
