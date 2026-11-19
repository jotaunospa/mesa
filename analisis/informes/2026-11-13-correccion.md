# Corrección del informe del 13-11: filas "dato rancio" (19-11)

El informe del 13-11 incluyó filas que el screener de Yahoo listó con un % viejo: a la hora de la foto
subían menos de 5% (o bajaban), sobre todo en la primera foto de la ventana (09:31–09:32). Desde el 19-11
esas filas se apartan (`lib/rancio.py`): el bot ya no las pone en la foto y el análisis las excluye de
fotos anteriores. Se recalculó el período del informe (05 al 13-11) con y sin ellas.

| Medición | Con filas rancias (informe del 13-11) | Sin filas rancias |
|---|---|---|
| Tramos: candidatas en la ventana | 234 | 200 |
| Ventana 09:35 → 10:15, bruto (promedio / mediana) | +5.25% / +2.36% | +6.28% / +2.80% |
| Ventana neta de costos (n, promedio / mediana) | 146: -5.28% / -6.85% | 120: -3.87% / -4.00% |
| Criterio de detención (aptas, retorno neto, IC 90%) | 8: -8.14% [-11.77, -4.51] | sin cambio |
| Línea base al azar | 194 operaciones, -0.51 R | 185 operaciones, -0.52 R |
| Setups activados (excursiones y variantes) | 39 | 38 |
| Variante: ordenado / no ordenado (R medio neto) | -1.77 / -2.61 | -1.86 / -2.04 |
| Reglas de la acción y umbrales del setup | sin cambio de veredicto | sin cambio de veredicto |

Lectura: las filas rancias empeoraban el promedio de la ventana (eran acciones que ya se estaban
desinflando). Sin ellas la ventana sigue negativa después de costos; ninguna conclusión cambia.
Primeras lecturas, no concluye (n < 30 en los grupos chicos).
