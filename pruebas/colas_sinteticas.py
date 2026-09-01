"""Pruebas del corte de la cola de «artículos relacionados», sin base de datos.

    python -m pruebas.colas_sinteticas
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.colas import _plano, corte, partir_cola  # noqa: E402

fallos = 0


def igual(que, esperado, real):
    global fallos
    if real != esperado:
        fallos += 1
        print(f"  FALLA  {que}\n         esperaba {esperado!r}\n"
              f"         obtuvo   {real!r}")
    else:
        print(f"  bien   {que}")


def donde(cuerpo: str):
    return corte(_plano(cuerpo), len(cuerpo))


ANALISIS = "El gobierno cedió terreno en la negociación. " * 40
COLA = ("\nARTÍCULOS RELACIONADOS:\nUna paz sin paz\n"
        "La Unión Europea frente a la crisis libia: un…\n"
        "La lucha de Libia por la estabilidad")


def main() -> int:
    print("\nCORTE DE LA COLA")
    c = ANALISIS + COLA
    limpio, cola = partir_cola(c)
    igual("la cola entera se va", True, cola.endswith("por la estabilidad"))
    igual("y empieza en el rótulo", True,
          cola.lstrip().startswith("ARTÍCULOS RELACIONADOS"))
    igual("no se lleva nada del análisis", True,
          limpio.endswith("en la negociación."))

    print("\nACENTOS Y MAYÚSCULAS")
    for variante in ("ARTÍCULOS RELACIONADOS:", "Artículos relacionados",
                     "ARTICULOS RELACIONADOS", "artículos Relacionados:"):
        c = ANALISIS + "\n" + variante + "\nOtro titular"
        igual(f"reconoce «{variante}»", True, donde(c) is not None)

    print("\nSALVAGUARDAS")
    igual("sin rótulo no corta", None, donde(ANALISIS))
    igual("rótulo en la primera mitad: no toca",
          None, donde("ARTÍCULOS RELACIONADOS es el título\n" + ANALISIS))
    igual("cuerpo vacío", None, donde(""))

    print("\nEL CORTE ES REVERSIBLE")
    for original in (ANALISIS + COLA,
                     ANALISIS + "   \n\n" + COLA.lstrip(),
                     ANALISIS.rstrip() + COLA):
        limpio, cola = partir_cola(original)
        igual("cuerpo + cola reconstruye el original", original, limpio + cola)

    print("\nAPLANADO")
    igual("conserva la longitud", len("Análisis Ñoño"), len(_plano("Análisis Ñoño")))
    igual("quita acentos y baja a minúsculas", "analisis nono", _plano("Análisis Ñoño"))

    print(f"\n{'TODO BIEN' if not fallos else str(fallos) + ' FALLOS'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
