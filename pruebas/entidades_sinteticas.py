"""Comprueba la lente de entidades sin necesidad de base de datos.

`cargar()` consulta PostgreSQL, pero `analizar()` trabaja sobre un diccionario,
así que se le puede dar uno fabricado a mano. La prueba planta cuatro casos que
cubren lo que la lente promete y lo que debe callar:

* una entidad que crece de verdad, del 2% al 35%;
* una que desaparece de golpe tras años de presencia;
* una estrictamente nueva, sin pasado en el archivo;
* tres constantes al 30%, que no deben aparecer por ningún lado.

Uso:  python -m pruebas.entidades_sinteticas
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentes.entidades import analizar, informe

ANYOS = [str(a) for a in range(2021, 2027)]
POR_ANYO = 120
CONSTANTES = ["Unión Europea", "Estados Unidos", "China"]
CRECE = {"2021": .02, "2022": .04, "2023": .08, "2024": .16, "2025": .25, "2026": .35}


def fabricar() -> dict:
    rng = random.Random(3)
    df: dict[tuple[str, str], dict[str, int]] = {}

    def anota(clave, per):
        df.setdefault(clave, {}).setdefault(per, 0)
        df[clave][per] += 1

    for per in ANYOS:
        for _ in range(POR_ANYO):
            for c in CONSTANTES:
                if rng.random() < 0.30:
                    anota(("actor", c), per)
            if rng.random() < CRECE[per]:
                anota(("tema", "populismo punitivo"), per)
            if per < "2026" and rng.random() < 0.30:
                anota(("actor", "Angela Merkel"), per)
            if per == "2026" and rng.random() < 0.20:
                anota(("lugar", "estrecho de Ormuz"), per)

    return {"periodos": ANYOS,
            "total": {p: POR_ANYO for p in ANYOS},
            "ricos": {p: POR_ANYO for p in ANYOS},
            "df": df}


def main() -> int:
    res = analizar(fabricar())
    print(informe(res))

    nombres = lambda k: {d["name"] for d in res.get(k, [])}
    fallos = []
    if "populismo punitivo" not in nombres("suben"):
        fallos.append("no detecta la entidad que crece")
    if "Angela Merkel" not in nombres("bajan"):
        fallos.append("no detecta la que desaparece")
    if "estrecho de Ormuz" not in nombres("nuevas"):
        fallos.append("no detecta la entidad nueva")
    for c in CONSTANTES:
        if c in nombres("suben") | nombres("bajan"):
            fallos.append(f"señala como novedad la constante «{c}»")

    print()
    if fallos:
        print("PRUEBA FALLIDA")
        for f in fallos:
            print("  ·", f)
        return 1
    print("PRUEBA SUPERADA: ve lo que sube, lo que se va y lo que llega nuevo,\n"
          "y no confunde con ninguno de ellos a lo que lleva años igual.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
