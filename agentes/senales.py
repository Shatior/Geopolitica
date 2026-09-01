"""Detector de señales débiles: lo que crece por debajo del radar.

Meta fija: términos de frecuencia baja cuya cuota crece de forma sostenida
durante varios periodos y que todavía no han sido titular de ningún análisis.
La intuición, tomada del propio archivo, es que un asunto suele instalarse
primero en el cuerpo de los textos y solo después llega a los titulares.

Criterio de rechazo: si nada cumple las condiciones, no hay señal esa semana.

Validación (retrospectiva): el detector se ejecuta tapando el archivo a partir
de un año dado y se comprueba si lo que señaló entonces creció después. Se
compara con un grupo de control de términos de frecuencia parecida que el
detector NO señaló. Si no gana al control, sus avisos sobre el presente no
merecen crédito, y así debe decirse.
"""
from __future__ import annotations

import random

from .base import Corpus, desduplicar, especificidad

MIN_TOTAL = 5          # menos que esto es anecdótico
MAX_TOTAL = 70         # más que esto ya no es "débil"
CRECIMIENTO_MIN = 2.0  # la cuota debe al menos duplicarse en la ventana


def _serie_cuotas(c: Corpus, termino: str, periodos: list[str]) -> list[float]:
    return [c.cuota(termino, p) for p in periodos]


def detectar(c: Corpus, ventana: int = 4, top: int = 12,
             hasta: str | None = None, exigir_fuera_titulares: bool = True) -> list[dict]:
    periodos = [p for p in c.periodos if hasta is None or p <= hasta]
    if len(periodos) < ventana + 1:
        return []
    ultimos = periodos[-ventana:]

    fuera: list[dict] = []
    for termino, total_global in c.df_total.items():
        # El total debe medirse solo hasta el corte, o la retrospectiva haría trampa.
        total = sum(c.df.get(termino, {}).get(p, 0) for p in periodos)
        if not (MIN_TOTAL <= total <= MAX_TOTAL):
            continue
        if exigir_fuera_titulares and termino in c.en_titulares:
            continue
        cuotas = _serie_cuotas(c, termino, ultimos)
        if cuotas[-1] <= 0:                       # tiene que estar vivo ahora
            continue
        subidas = sum(1 for i in range(1, len(cuotas)) if cuotas[i] >= cuotas[i - 1])
        if subidas < len(cuotas) - 2:             # tolera un tropiezo
            continue
        arranque = max(cuotas[0], 1e-9)
        crecimiento = cuotas[-1] / arranque
        if cuotas[0] == 0 and cuotas[-1] > 0:
            crecimiento = float("inf")
        if crecimiento < CRECIMIENTO_MIN:
            continue
        fuera.append({
            "termino": termino, "total": total,
            "n_ultimo": c.df.get(termino, {}).get(ultimos[-1], 0),
            "cuotas": cuotas, "periodos": ultimos,
            "crecimiento": crecimiento,
            "impulso": cuotas[-1] - cuotas[0],
        })

    fuera.sort(key=lambda d: (-d["impulso"], -(" " in d["termino"]),
                              especificidad(d["termino"], c.df_total)))
    docs = c.docs_de({d["termino"] for d in fuera[:80]})
    return desduplicar(fuera[:80], docs)[:top]


def retrospectiva(c: Corpus, corte: str, ventana: int = 4,
                  horizonte: int = 3, semilla: int = 7) -> dict:
    """¿Habría acertado el detector si lo hubiéramos tenido en su día?"""
    posteriores = [p for p in c.periodos if p > corte][:horizonte]
    if not posteriores:
        return {"posible": False}

    señalados = detectar(c, ventana=ventana, top=25, hasta=corte)
    if not señalados:
        return {"posible": False, "motivo": f"no hubo señales en el corte {corte}"}

    def crecio(termino: str) -> bool:
        antes = c.cuota(termino, corte)
        despues = max(c.cuota(termino, p) for p in posteriores)
        return despues >= max(antes, 1e-9) * 1.5

    aciertos = [d["termino"] for d in señalados if crecio(d["termino"])]

    # Control: términos con soporte parecido que el detector NO señaló.
    nombres = {d["termino"] for d in señalados}
    candidatos = [
        t for t, _ in c.df_total.items()
        if t not in nombres
        and MIN_TOTAL <= sum(c.df.get(t, {}).get(p, 0) for p in c.periodos if p <= corte) <= MAX_TOTAL
    ]
    random.Random(semilla).shuffle(candidatos)
    control = candidatos[:200]
    aciertos_control = [t for t in control if crecio(t)]

    return {
        "posible": True, "corte": corte, "horizonte": posteriores,
        "n_señalados": len(señalados), "n_aciertos": len(aciertos),
        "tasa": len(aciertos) / len(señalados),
        "tasa_control": (len(aciertos_control) / len(control)) if control else 0.0,
        "ejemplos": aciertos[:8],
    }


def informe(c: Corpus, señales: list[dict], val: dict | None) -> str:
    out = ["=" * 70, "DETECTOR DE SEÑALES DÉBILES", "=" * 70]

    if val and val.get("posible"):
        mejora = val["tasa"] / val["tasa_control"] if val["tasa_control"] else float("inf")
        veredicto = ("SUPERA al azar" if mejora >= 1.3 else
                     "NO supera al azar — no fiarse de sus avisos")
        out += [
            f"Validación retrospectiva · corte en {val['corte']}, "
            f"horizonte {val['horizonte'][0]}–{val['horizonte'][-1]}",
            f"  De {val['n_señalados']} términos señalados entonces, "
            f"{val['n_aciertos']} crecieron después ({val['tasa']*100:.0f}%).",
            f"  Grupo de control con soporte parecido: {val['tasa_control']*100:.0f}%.",
            f"  → {veredicto}" + (f" (×{mejora:.1f})" if mejora != float('inf') else ""),
        ]
        if val["ejemplos"]:
            out.append("  Acertó con: " + ", ".join(val["ejemplos"][:6]))
        out.append("")
    else:
        motivo = (val or {}).get("motivo", "corpus demasiado corto")
        out += [f"Validación retrospectiva: no ha sido posible ({motivo}).", ""]

    if not señales:
        out.append("Sin señales que cumplan los criterios en este periodo.")
        return "\n".join(out)

    out.append(f"SEÑALES VIVAS ({len(señales)}) · frecuencia baja, crecimiento "
               f"sostenido, nunca en titular")
    out.append(f"{'término':38} {'total':>6} {'ahora':>6}   evolución de la cuota")
    for d in señales:
        serie = " → ".join(f"{x*100:.1f}%" for x in d["cuotas"])
        out.append(f"  {d['termino'][:36]:38} {d['total']:5}  {d['n_ultimo']:5}   {serie}")
    return "\n".join(out)
