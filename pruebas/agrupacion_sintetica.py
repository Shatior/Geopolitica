"""Prueba de la agrupación de la lista por número, sin base de datos.

    python -m pruebas.agrupacion_sintetica
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.app import agrupar_por_numero  # noqa: E402

fallos = 0


def igual(que, esperado, real):
    global fallos
    if real != esperado:
        fallos += 1
        print(f"  FALLA  {que}: esperaba {esperado!r}, obtuvo {real!r}")
    else:
        print(f"  bien   {que}")


def pieza(id_, pub, issue, num=None):
    return {"id": id_, "pub_slug": pub, "issue_id": issue, "pub_name": pub,
            "issue_number": num, "published_date": None}


def formas(grupos):
    return [len(g["piezas"]) for g in grupos]


def main() -> int:
    print("\nAGRUPACIÓN POR NÚMERO")
    igual("cinco piezas de un número dan un grupo", [5],
          formas(agrupar_por_numero([pieza(i, "is", 700) for i in range(5)])))
    igual("dos números seguidos dan dos grupos", [5, 5],
          formas(agrupar_por_numero(
              [pieza(i, "is", 700) for i in range(5)]
              + [pieza(i, "is", 701) for i in range(5)])))
    igual("sin resultados, sin grupos", [], formas(agrupar_por_numero([])))

    print("\nLO QUE NO DEBE JUNTARSE")
    igual("mismo número en publicaciones distintas", [1, 1],
          formas(agrupar_por_numero([pieza(1, "is", 7), pieza(2, "pe", 7)])))
    igual("piezas sin número no se agrupan entre sí", [1, 1, 1],
          formas(agrupar_por_numero([pieza(i, "is", None) for i in range(3)])))
    igual("no reordena: el mismo número separado por otro queda en dos grupos",
          [1, 1, 1],
          formas(agrupar_por_numero(
              [pieza(1, "is", 700), pieza(2, "is", 900), pieza(3, "is", 700)])))

    print("\nLA CABECERA SALE DE LA PRIMERA PIEZA")
    g = agrupar_por_numero([pieza(1, "is", 700, num=1479),
                            pieza(2, "is", 700, num=1479)])
    igual("un solo grupo", 1, len(g))
    igual("con su número", 1479, g[0]["issue_number"])
    igual("y las dos piezas dentro", [1, 2], [p["id"] for p in g[0]["piezas"]])

    print(f"\n{'TODO BIEN' if not fallos else str(fallos) + ' FALLOS'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
