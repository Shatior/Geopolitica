"""Corpus sintético para comprobar que las lentes miden lo que dicen medir.

No sustituye a ejecutarlas contra el archivo real, pero sí permite plantar a
propósito los tres engaños que se han detectado y verificar que ninguno pasa:

1. Una tendencia real («corredor artico»), que debe aparecer.
2. Un término constante («cumbre europea»), que no debe aparecer.
3. Un ARTEFACTO DE DENSIDAD: en el último periodo los análisis son el doble
   de largos. Sin corregir, todo el vocabulario de relleno parece dispararse.
   Es lo que ocurrió de verdad con el corpus de 2026.

Uso:  python -m pruebas.lentes_sinteticas
"""
from __future__ import annotations

import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentes import cronista, senales
from agentes.base import corpus_desde_filas

RELLENO = ("acuerdo bilateral tension frontera ministro exteriores reunion "
           "delegacion informe conflicto region alianza tratado sancion "
           "diplomacia mercado energia frontera oriental gobierno coalicion "
           "presupuesto militar reforma institucional debate parlamento").split()

TEMAS = ["oriente medio", "union europea", "estados unidos", "america latina",
         "asia pacifico", "africa occidental", "europa oriental"]


def cuerpo(rng: random.Random, palabras: int, extra: list[str]) -> str:
    frases = []
    restantes = palabras
    while restantes > 0:
        n = min(rng.randint(8, 16), restantes)
        frases.append(" ".join(rng.choice(RELLENO) for _ in range(n)))
        restantes -= n
    texto = ". ".join(frases) + "."
    for e in extra:
        texto += f" El asunto del {e} ocupa la agenda."
    return texto


def generar(artefacto: bool) -> list[dict]:
    """1.500 análisis de 2014 a 2026. Con artefacto=True, los de 2026 son el
    doble de largos, sin que cambie ni un ápice la agenda."""
    rng = random.Random(11)
    filas, ident = [], 0
    for anyo in range(2014, 2027):
        n = 45 if anyo < 2021 else 240          # el salto de densidad real
        for i in range(n):
            ident += 1
            extra = []
            # Tendencia plantada: crece de 2023 a 2026.
            prob = {2023: 0.05, 2024: 0.12, 2025: 0.22, 2026: 0.35}.get(anyo, 0.0)
            if rng.random() < prob:
                extra.append("corredor artico")
            # Constante: siempre en el 20%, no debe salir en ninguna lente.
            if rng.random() < 0.20:
                extra.append("cumbre europea")
            largo = 120
            if artefacto and anyo == 2026:
                largo = 240                      # mismos temas, doble de texto
            filas.append({
                "id": ident,
                "published_date": date(anyo, 1 + (i * 11) % 12, 1 + (i * 7) % 28),
                "title": f"{rng.choice(TEMAS)}: nota {ident}",
                "body": cuerpo(rng, largo, extra),
            })
    return filas


def main() -> int:
    fallos = []
    for artefacto in (False, True):
        etiqueta = "CON artefacto de densidad" if artefacto else "sin artefacto"
        c = corpus_desde_filas(generar(artefacto))
        f2026 = c.escala["2026"]
        print(f"\n{'=' * 66}\nCORPUS SINTÉTICO · {etiqueta}\n{'=' * 66}")
        print(f"  términos/análisis 2025: {c.densidad['2025']:.0f}   "
              f"2026: {c.densidad['2026']:.0f}   factor 2026: {f2026:.2f}")

        if artefacto and f2026 < 1.3:
            fallos.append("el artefacto no se detecta: el factor de 2026 "
                          f"debería ser claramente >1 y es {f2026:.2f}")
        if not artefacto and not (0.8 < f2026 < 1.25):
            fallos.append(f"sin artefacto el factor debería rondar 1 y es {f2026:.2f}")

        res = cronista.analizar(c)
        suben = [d["termino"] for d in res.get("suben", [])]
        print("  cronista ▲:", ", ".join(suben[:8]) or "(nada)")

        if not any("artico" in t or "corredor" in t for t in suben):
            fallos.append(f"[{etiqueta}] la tendencia plantada no aparece")
        if any("cumbre" in t or "europea" in t for t in suben):
            fallos.append(f"[{etiqueta}] el término constante aparece como tendencia")
        # Lo decisivo: el relleno no debe colarse ni con el artefacto puesto.
        colados = [t for t in suben if all(w in RELLENO for w in t.split())]
        if colados:
            fallos.append(f"[{etiqueta}] palabras de relleno señaladas como "
                          f"tendencia: {', '.join(colados[:5])}")

    print(f"\n{'=' * 66}")
    if fallos:
        print("PRUEBA FALLIDA")
        for f in fallos:
            print("  ·", f)
        return 1
    print("PRUEBA SUPERADA: la tendencia se ve, la constante no, y el artefacto\n"
          "de densidad no arrastra al vocabulario de relleno.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
