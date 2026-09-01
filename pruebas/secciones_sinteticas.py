"""Pruebas del troceado de los PDF, sin PDF.

    python -m pruebas.secciones_sinteticas

El PDF de un número es material de pago y no se versiona, así que el troceado
se prueba sobre fragmentos escritos a mano con la misma forma que los que
devuelve la maqueta: antetítulo, titular, capitular y cuerpo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rescate.run import GENERICO, emparejar  # noqa: E402
from rescate.secciones import partir_trozos  # noqa: E402

fallos = 0


def igual(que, esperado, real):
    global fallos
    if real != esperado:
        fallos += 1
        print(f"  FALLA  {que}\n         esperaba {esperado!r}\n"
              f"         obtuvo   {real!r}")
    else:
        print(f"  bien   {que}")


def relleno(n: int) -> str:
    return ("La política exterior del país cambió de rumbo aquel año. " * 40)[:n]


def test_troceado():
    print("\nTROCEADO")
    trozos = [
        ("antetitulo", "BANCA"),
        ("titular", "Crimen –casi– sin"),
        ("titular", " castigo"),          # el titular viene partido
        ("cuerpo", "T"), ("cuerpo", "AN solo unas semanas. "),
        ("cuerpo", relleno(900)),
        ("antetitulo", "ORIENTE PRÓXIMO"),
        ("titular", "Duelo en Líbano"),
        ("cuerpo", relleno(900)),
    ]
    secs = partir_trozos(trozos)
    igual("dos secciones", 2, len(secs))
    igual("antetítulo de la primera", "BANCA", secs[0]["antetitulo"])
    igual("titular recompuesto", "Crimen –casi– sin castigo", secs[0]["titulo"])
    igual("capitular pegada", True, secs[0]["cuerpo"].startswith("TAN solo"))
    igual("antetítulo no se arrastra", "ORIENTE PRÓXIMO", secs[1]["antetitulo"])
    igual("orden", [1, 2], [s["orden"] for s in secs])


def test_descartes():
    print("\nDESCARTES")
    secs = partir_trozos([
        ("titular", "Reclamo de portada"), ("cuerpo", "Cuatro palabras."),
        ("titular", "Sección de verdad"), ("cuerpo", relleno(900)),
    ])
    igual("el reclamo corto se descarta", 1, len(secs))
    igual("sobrevive la buena", "Sección de verdad", secs[0]["titulo"])
    igual("cuerpo sin titular previo se ignora",
          0, len(partir_trozos([("cuerpo", relleno(900))])))


def test_guiones():
    print("\nGUIONES DE FIN DE LÍNEA")
    secs = partir_trozos([
        ("titular", "T"),
        ("cuerpo", "las autoridades impu- sieran una sanción. "),
        ("cuerpo", "El acuerdo –que nadie firmó– cayó. "),
        ("cuerpo", relleno(700)),
    ])
    c = secs[0]["cuerpo"]
    igual("rehace la palabra partida", True, "impusieran" in c)
    igual("respeta el guión del texto", True, "–que nadie firmó–" in c)


def test_emparejar():
    print("\nEMPAREJAMIENTO CON LO YA GUARDADO")
    arranque = "Tan sólo unas semanas después de que las autoridades de EE UU"
    secciones = [
        {"cuerpo": "TAN SOLO unas semanas, después de que las autoridades de "
                   "EE UU impusieran…", "titulo": "Crimen sin castigo"},
        {"cuerpo": "El violento desalojo por la policía ucraniana de Kiev…",
         "titulo": "El abrazo del oso"},
    ]
    articulos = [{"id": 7, "title": "#ISPE 870. 16 diciembre 2013",
                  "body": arranque + " impusieran…"}]
    igual("empareja la gemela por el arranque", {0: 7},
          emparejar(secciones, articulos))
    igual("no empareja lo que no casa", {},
          emparejar([secciones[1]], articulos))
    igual("ante dos candidatos no toca ninguno", {},
          emparejar([secciones[0]], articulos + [dict(articulos[0], id=8)]))


def test_titulares_genericos():
    print("\nTITULARES QUE HAY QUE CORREGIR")
    for t in ("#ISPE 870. 16 diciembre 2013", "ISPE 824",
              "Informe Semanal de Política Exterior 872"):
        igual(f"genérico: {t[:32]}", True, bool(GENERICO.match(t)))
    for t in ("Crimen –casi– sin castigo", "Santos y Maduro, vidas paralelas"):
        igual(f"propio: {t[:32]}", False, bool(GENERICO.match(t)))


def main() -> int:
    test_troceado()
    test_descartes()
    test_guiones()
    test_emparejar()
    test_titulares_genericos()
    print(f"\n{'TODO BIEN' if not fallos else str(fallos) + ' FALLOS'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
