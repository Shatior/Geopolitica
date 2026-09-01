"""Cronista de la atención: qué sube y qué baja respecto a su propia historia.

Meta fija: identificar los términos cuya cuota de atención en el último
periodo se desvía más de su base histórica, con soporte suficiente para que
la desviación no sea casualidad.

Dos cautelas aprendidas al ejecutarlo contra el archivo real:

* Se comparan **cuotas corregidas por la densidad del periodo**. Sin corregir,
  un año cuyos análisis son más largos hace subir a la vez a «progresivamente»,
  «sostuvo» y «persisten»: no cambió la agenda, cambió la extensión del texto.
* Se ordena por **cuánto cambia la cuota en puntos**, no por el cociente. El
  cociente premia a los términos que partían de casi cero, así que colocaba
  arriba hallazgos anecdóticos y dejaba abajo los verdaderamente masivos.
* Se exige **significación estadística** con corrección por el número de
  términos examinados. Mirando decenas de miles de términos, unos cuantos se
  desvían mucho por azar; sin esta prueba la lente los publicaría como
  hallazgos.

Criterio de rechazo: si ningún término supera el soporte mínimo y el umbral
de desviación, no hay pieza. Es preferible el silencio a un titular fabricado.
"""
from __future__ import annotations

from .base import Corpus, barra, cola_binomial, desduplicar, especificidad

SOPORTE_MIN = 6        # análisis mínimos con el término en el periodo actual
BASE_MIN = 3           # periodos mínimos de historia para comparar
LIFT_MIN = 1.6         # cuánto debe desviarse para merecer mención
ALFA = 0.01            # riesgo aceptado de señalar algo que solo era azar


def analizar(c: Corpus, periodos_base: int = 5, top: int = 12) -> dict:
    if len(c.periodos) < BASE_MIN + 1:
        return {"suficiente": False, "motivo": "historia insuficiente"}

    actual = c.periodos[-1]
    base = c.periodos[-(periodos_base + 1):-1]
    if len(base) < BASE_MIN:
        return {"suficiente": False, "motivo": "base histórica corta"}

    n_docs = c.docs_por_periodo.get(actual, 0)
    factor = c.escala.get(actual, 1.0)

    # Cuántos términos se examinan de verdad, para repartir el riesgo entre
    # todos ellos (Bonferroni): con 20.000 candidatos, un umbral del 1% sin
    # corregir dejaría pasar 200 casualidades.
    examinados = sum(1 for t, tot in c.df_total.items() if tot >= SOPORTE_MIN)
    umbral = ALFA / max(examinados, 1)

    suben, bajan = [], []
    for termino, total in c.df_total.items():
        if total < SOPORTE_MIN:
            continue
        n_actual = c.df.get(termino, {}).get(actual, 0)
        cuota_actual = c.cuota_n(termino, actual)
        cuotas_base = [c.cuota_n(termino, p) for p in base]
        media_base = sum(cuotas_base) / len(cuotas_base)
        if media_base <= 0:
            continue
        # Cuota que cabría esperar en este periodo si nada hubiera cambiado,
        # devuelta a la escala cruda para poder contarla contra los análisis.
        esperada = min(media_base * factor, 0.999)

        # Suben: exigimos soporte real en el periodo actual.
        if n_actual >= SOPORTE_MIN:
            lift = cuota_actual / media_base
            if lift >= LIFT_MIN:
                pv = cola_binomial(n_actual, n_docs, esperada, superior=True)
                if pv <= umbral:
                    suben.append({
                        "termino": termino, "n": n_actual,
                        "cuota": c.cuota(termino, actual),
                        "base": media_base, "lift": lift, "p": pv,
                        "delta": cuota_actual - media_base,
                    })
        # Bajan: exigimos que ANTES fuera relevante, aunque ahora no aparezca.
        media_n_base = sum(c.df.get(termino, {}).get(p, 0) for p in base) / len(base)
        if media_n_base >= SOPORTE_MIN:
            lift = cuota_actual / media_base
            if lift <= 1 / LIFT_MIN:
                pv = cola_binomial(n_actual, n_docs, esperada, superior=False)
                if pv <= umbral:
                    bajan.append({
                        "termino": termino, "n": n_actual,
                        "cuota": c.cuota(termino, actual),
                        "base": media_base, "lift": lift, "p": pv,
                        "delta": media_base - cuota_actual,
                    })

    # Manda el tamaño del cambio; a igualdad, el bigrama por ser más específico.
    esp = lambda d: especificidad(d["termino"], c.df_total)
    suben.sort(key=lambda d: (-d["delta"], -(" " in d["termino"]), esp(d)))
    bajan.sort(key=lambda d: (-d["delta"], -(" " in d["termino"]), esp(d)))

    # Un mismo fenómeno suele disparar varios términos solapados: se deja uno.
    finalistas = {d["termino"] for d in suben[:80]} | {d["termino"] for d in bajan[:80]}
    docs = c.docs_de(finalistas)
    suben = desduplicar(suben[:80], docs)
    bajan = desduplicar(bajan[:80], docs)
    return {
        "suficiente": bool(suben or bajan),
        "periodo": actual, "base": f"{base[0]}–{base[-1]}",
        "docs_actual": c.docs_por_periodo.get(actual, 0),
        "examinados": examinados,
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
        "Cuotas corregidas por la densidad de cada periodo; se ordena por el "
        "cambio\nen puntos, no por el cociente. Umbral: "
        f"≥{SOPORTE_MIN} análisis, desviación ≥{LIFT_MIN}× y significación\n"
        f"al {ALFA*100:.0f}% repartida entre los {res.get('examinados', 0)} "
        f"términos examinados.",
        "",
        "▲ MÁS ATENCIÓN DE LA HABITUAL",
    ]
    mx = max([d["delta"] for d in res["suben"]], default=1)
    for d in res["suben"]:
        out.append(f"  +{d['delta']*100:4.1f} pt  ×{d['lift']:5.1f}  "
                   f"{d['termino'][:34]:36} {d['n']:3} análisis "
                   f"({d['cuota']*100:4.1f}%)  {barra(d['delta'], mx, 14)}")
    if not res["suben"]:
        out.append("  (ninguno supera el umbral)")

    out += ["", "▼ MENOS ATENCIÓN DE LA HABITUAL"]
    for d in res["bajan"]:
        out.append(f"  −{d['delta']*100:4.1f} pt  ×{d['lift']:5.2f}  "
                   f"{d['termino'][:34]:36} {d['n']:3} análisis "
                   f"({d['cuota']*100:4.1f}%, base {d['base']*100:4.1f}%)")
    if not res["bajan"]:
        out.append("  (ninguno supera el umbral)")
    return "\n".join(out)
