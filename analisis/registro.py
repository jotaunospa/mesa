"""Registro de aprendizaje: guarda cada GO, LISTO, VIGILAR y "casi" con lo que pasó después.

Uso: python analisis/registro.py veredictos.json DIR_DATOS RULES_V SALIDA_DIR
Escribe SALIDA_DIR/AAAA-MM-DD.jsonl (un archivo por día, una línea por acción, perfil y foto).
Se acumula semana a semana en analisis/registro/ para buscar patrones con muchos casos.
El veredicto queda congelado con la versión de reglas de ese momento.
"""
import json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B

ETAPA = {"entrada": "GO", "candidato": "LISTO", "vigilar": "VIGILAR"}


def despues(v, hora, mins):
    """% máximo, mínimo y último en los `mins` minutos siguientes a la foto, contra el precio de la foto."""
    antes = [c for c in v if c[0] <= hora]
    if not antes:
        return None
    p0 = antes[-1][4]
    h0 = int(hora[:2]) * 60 + int(hora[3:])
    fin = f"{(h0 + mins) // 60:02d}:{(h0 + mins) % 60:02d}"
    sig = [c for c in v if hora < c[0] <= fin]
    if not sig or not p0:
        return None
    pct = lambda x: round((x / p0 - 1) * 100, 2)
    return {"max": pct(max(c[2] for c in sig)), "min": pct(min(c[3] for c in sig)), "ultimo": pct(sig[-1][4])}


def main(verd, datos, rules_v, out):
    V = json.load(open(verd))
    por_dia = defaultdict(list)
    for r in V:
        if r["hora"] > "10:20":
            continue  # fotos de la tarde: fuera de la ventana de la Mesa
        casi = {c.split(":")[0]: c.split(":")[1] for c in r.get("casi") or []}
        for p in ("cons", "mom"):
            etapa = ETAPA.get(r[p]["stage"]) or ("CASI" if p in casi else None)
            if not etapa:
                continue
            v = B.velas(datos, r["dia"], r["tk"]) or []
            ev = {"dia": r["dia"], "hora": r["hora"], "tk": r["tk"], "perfil": p, "etapa": etapa, "reglas": rules_v,
                  "falla": r[p]["fails"], "casi_por": casi.get(p), "apto_escaner": r.get("apto"),
                  "setup": r.get("setup"), "despues_15": despues(v, r["hora"], 15), "despues_30": despues(v, r["hora"], 30)}
            s = r.get("setup")
            if s and v:
                B.fin.stop, B.fin.entrada = s["stop"], s["entry"]
                res = B.simular(v, r["hora"], s)
                ev["setup_R"] = None if res is None else {"R": res[0], "motivo": res[1], "entra": res[2]}
            por_dia[r["dia"]].append(ev)
    os.makedirs(out, exist_ok=True)
    for dia, L in por_dia.items():
        dest = os.path.join(out, f"{dia}.jsonl")
        if os.path.exists(dest):  # congelado: un día ya registrado no se reescribe con reglas posteriores
            continue
        with open(dest, "w") as f:
            for ev in L:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"registro: {sum(map(len, por_dia.values()))} casos en {len(por_dia)} días -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
