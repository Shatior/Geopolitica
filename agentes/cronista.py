"""Cronista de la atención: qué sube y qué baja respecto a su propia historia.

Meta fija: identificar los términos cuya cuota de atención en el último
periodo se desvía más de su base histórica, con soporte suficiente para que
la desviación no sea casualidad.

Criterio de rechazo: si ningún término supera el soporte mínimo y el umbral
de desviación, no hay pieza. Es preferible el silencio a un titular fabricado.
"""
from __future__ import annotations

from .base import Corpus, barra, desduplicar, especificidad

SOPORTE_MIN = 6        # análisis mínimos con el término en el periodo actual
BASE_MIN = 3           # periodos mínimos de historia para comparar
LIFT_MIN = 1.6         # cuánto debe desviarse para merecer mención


def analizar(c: Corpus, periodos_base: int = 5, top: int = 12) -> dict:
    if len(c.periodos) < BASE_MIN + 1:
        return {"suficiente": False, "motivo": "historia insuficiente"}

    actual = c.periodos[-1]
    base = c.periodos[-(periodos_base + 1):-1]
    if len(base) < BASE_MIN:
        return {"suficiente": False, "motivo": "base histórica corta"}

    suben, bajan = [], []
    for termino, total in c.df_total.items():
        if total < SOPORTE_MIN:
            continue
        n_actual = c.df.get(termino, {}).get(actual, 0)
        cuota_actual = c.cuota(termino, actual)
        cuotas_base = [c.cuota(termino, p) for p in base]
        media_base = sum(cuotas_base) / len(cuotas_base)

        # Suben: exigimos soporte real en el periodo actual.
        if n_actual >= SOPORTE_MIN and media_base > 0:
            lift = cuota_actual / media_base
            if lift >= LIFT_MIN:
                suben.append({
                    "termino": termino, "n": n_actual, "cuota": cuota_actual,
                    "base": media_base, "lift": lift,
                })
        # Bajan: exigimos que ANTES fuera relevante, aunque ahora no aparezca.
        media_n_base = sum(c.df.get(termino, {}).get(p, 0) for p in base) / len(base)
        if media_n_base >= SOPORTE_MIN and media_base > 0:
            lift = cuota_actual / media_base
            if lift <= 1 / LIFT_MIN:
                bajan.append({
                    "termino": termino, "n": n_actual, "cuota": cuota_actual,
                    "base": media_base, "lift": lift,
                })

    # A igualdad de desviación gana el bigrama, más específico e informativo.
    esp = lambda d: especificidad(d["termino"], c.df_total)
    suben.sort(key=lambda d: (-d["lift"], -(" " in d["termino"]), esp(d)))
    bajan.sort(key=lambda d: (d["lift"], -(" " in d["termino"]), esp(d)))

    # Un mismo fenómeno suele disparar varios términos solapados: se deja uno.
    finalistas = {d["termino"] for d in suben[:80]} | {d["termino"] for d in bajan[:80]}
    docs = c.docs_de(finalistas)
    suben = desduplicar(suben[:80], docs)
    bajan = desduplicar(bajan[:80], docs)
    return {
        "suficiente": bool(suben or bajan),
        "periodo": actual, "base": f"{base[0]}–{base[-1]}",
        "docs_actual": c.docs_por_periodo.get(actual, 0),
        "suben": suben[:top], "bajan": bajan[:top],
    }


def informe(c: Corpus, res: dict) -> str:
    if not res.get("suficiente"):
        return f"CRONISTA — sin hallazgos ({res.get('motivo', 'nada supera el umbral')})."

    out = [
        "=" * 70,
        f"CRONISTA DE LA ATENCIÓN · periodo {res['periodo']} "
        f"({res['docs_actual']} análisis) frente a {res['base']}",
        "=" * 70,
        f"Se comparan cuotas (análisis con el término ÷ análisis del periodo), "
        f"no frecuencias brutas.\nUmbral: ≥{SOPORTE_MIN} análisis y desviación ≥{LIFT_MIN}×.",
        "",
        "▲ MÁS ATENCIÓN DE LA HABITUAL",
    ]
    mx = max([d["lift"] for d in res["suben"]], default=1)
    for d in res["suben"]:
        out.append(f"  ×{d['lift']:4.1f}  {d['termino'][:38]:40} "
                   f"{d['n']:3} análisis ({d['cuota']*100:4.1f}%)  {barra(d['lift'], mx)}")
    if not res["suben"]:
        out.append("  (ninguno supera el umbral)")

    out += ["", "▼ MENOS ATENCIÓN DE LA HABITUAL"]
    for d in res["bajan"]:
        caida = (1 - d["lift"]) * 100
        out.append(f"  −{caida:4.0f}%  {d['termino'][:38]:40} "
                   f"{d['n']:3} análisis ({d['cuota']*100:4.1f}%, base {d['base']*100:4.1f}%)")
    if not res["bajan"]:
        out.append("  (ninguno supera el umbral)")
    return "\n".join(out)
