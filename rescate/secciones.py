"""Partir el PDF de un Informe Semanal en las secciones que lo componen.

    python -m rescate.secciones data/pdfs/informe-semanal-870.pdf

Hasta 2020 el sitio publicaba en web una sola sección de cada informe: del
número 870, de siete secciones y 25.260 caracteres, la web dio 3.259. Las
otras seis nunca estuvieron en internet. Están en el PDF y solo ahí, así que
los doce años de 2009 a 2020 se rescatan de aquí o no se rescatan.

El troceado no es heurístico. La maqueta usa tipografías distintas para el
texto y para el mobiliario, y eso da un criterio exacto:

    cuerpo      Century-Book/Bold/BookItalic a 11 pt (y 8,2 pt en las
                versalitas de siglas: «EE UU», «JP Morgan»)
    capitular   la misma familia a 47 o 65 pt: es la primera letra de la
                sección, así que cuenta como cuerpo y va delante
    titular     la misma familia entre 17 y 40 pt
    antetítulo  Futura-ExtraBold a 8 pt («BANCA», «ORIENTE PRÓXIMO») o,
                en la sección de portada, Century-BookItalic a 16 pt
    mobiliario  Century-Light, Carousel, Helvetica, ZapfDingbats: sumario,
                cabecera, créditos, pies de foto, publicidad. Se descarta.

Una sección empieza en cada titular y termina donde empieza el siguiente.

Los espacios no vienen dados: la maqueta coloca cada fragmento por su
posición, de modo que «EE», «UU» e «impu-» son tres fragmentos pegados que
en el papel están separados. El espacio se deduce del hueco entre sus cajas,
que es también lo que evita que la capitular quede suelta («T» + «AN solo»
tiene que dar «TAN solo», no «T AN solo»).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FUENTES_CUERPO = {"Century-Book", "Century-Bold", "Century-BookItalic"}
FUENTE_ANTETITULO = "Futura-ExtraBold"
# La cabecera de la revista y los rótulos van en estas familias; ningún
# titular de sección las usa, así que sirven para descartarlas de una vez.
FUENTES_MOBILIARIO = {"Carousel", "Helvetica", "Helvetica-Bold", "ZapfDingbats",
                      "Century-Light", "EuroSans-Regular"}

CUERPO_MIN, CUERPO_MAX = 8.0, 12.0
TITULAR_MIN, TITULAR_MAX = 17.0, 40.0
CAPITULAR_MIN = 40.0
ANTETITULO_MAX = 16.5

# Un hueco menor que esta fracción del cuerpo de letra es puro ajuste
# tipográfico, no un espacio.
HUECO_MINIMO = 0.22

# Una sección de verdad tiene al menos un par de párrafos. Por debajo de esto
# es un reclamo de portada o un resto de maqueta, no un análisis.
CUERPO_MINIMO = 600


def _clase(fuente: str, tam: float) -> str | None:
    if fuente == FUENTE_ANTETITULO and tam <= ANTETITULO_MAX:
        return "antetitulo"
    if fuente == "Century-BookItalic" and 15 <= tam <= ANTETITULO_MAX:
        return "antetitulo"
    if TITULAR_MIN <= tam <= TITULAR_MAX and fuente not in FUENTES_MOBILIARIO:
        return "titular"          # «Apuntes» se compone en Times, no en Century
    if fuente not in FUENTES_CUERPO:
        return None
    if CUERPO_MIN <= tam <= CUERPO_MAX or tam >= CAPITULAR_MIN:
        return "cuerpo"
    return None                   # créditos y pies en la misma familia


def _trozos(ruta: Path):
    """Los fragmentos del PDF en orden de lectura, ya clasificados y con el
    separador que les corresponde delante."""
    import pymupdf

    doc = pymupdf.open(ruta)
    previo = None                 # (caja, tamaño, línea) del último emitido
    for n, pagina in enumerate(doc):
        salto_de_pagina = previo is not None
        for bloque in pagina.get_text("dict")["blocks"]:
            for linea in bloque.get("lines", []):
                for span in linea["spans"]:
                    if not span["text"].strip():
                        continue
                    clase = _clase(span["font"], span["size"])
                    if clase is None:
                        continue
                    caja, tam = span["bbox"], span["size"]
                    if previo is None or previo[1] >= CAPITULAR_MIN:
                        sep = ""          # principio, o justo tras la capitular
                    elif salto_de_pagina:
                        sep = " "         # una sección puede seguir en la
                        salto_de_pagina = False   # página siguiente
                    elif previo[2] is not linea:
                        sep = " "         # cambio de línea o de columna
                    elif caja[0] - previo[0][2] > HUECO_MINIMO * tam:
                        sep = " "
                    else:
                        sep = ""
                    previo = (caja, tam, linea)
                    yield clase, sep + span["text"]


def _limpiar(cuerpo: str) -> str:
    """Rehace las palabras que la maqueta parte a final de línea y normaliza
    los espacios, sin tocar los guiones que sí son del texto."""
    t = cuerpo.replace("­", "")
    t = re.sub(r"(\w)-\s+(?=[a-záéíóúüñ])", r"\1", t)   # «impu- sieran»
    t = re.sub(r"[ \t ]+", " ", t)
    return t.strip()


def partir(ruta: str | Path) -> list[dict]:
    """Las secciones del número, en orden."""
    return partir_trozos(_trozos(Path(ruta)))


def partir_trozos(trozos) -> list[dict]:
    """Igual, pero sobre fragmentos ya clasificados: así el troceado se puede
    probar sin un PDF delante, que además no se puede versionar."""
    secciones: list[dict] = []
    antetitulo = ""
    actual: dict | None = None

    for clase, texto in trozos:
        if clase == "antetitulo":
            antetitulo = texto.strip()
        elif clase == "titular":
            # Un titular puede venir partido en varios fragmentos seguidos.
            if actual is not None and not actual["cuerpo"]:
                actual["titulo"] += " " + texto.strip()
                continue
            actual = {"antetitulo": antetitulo, "titulo": texto.strip(),
                      "cuerpo": ""}
            secciones.append(actual)
            antetitulo = ""
        elif actual is not None:
            actual["cuerpo"] += texto

    for i, s in enumerate(secciones, 1):
        s["orden"] = i
        s["titulo"] = _limpiar(s["titulo"])
        s["cuerpo"] = _limpiar(s["cuerpo"])
    return [s for s in secciones if len(s["cuerpo"]) >= CUERPO_MINIMO]


def main(argv=None) -> int:
    rutas = argv if argv is not None else sys.argv[1:]
    if not rutas:
        print(__doc__)
        return 2
    for ruta in rutas:
        secciones = partir(ruta)
        total = sum(len(s["cuerpo"]) for s in secciones)
        print(f"\n{ruta}: {len(secciones)} secciones, {total:,} caracteres")
        for s in secciones:
            ante = f"{s['antetitulo']} · " if s["antetitulo"] else ""
            print(f"  {s['orden']}. [{len(s['cuerpo']):5,}] {ante}{s['titulo']}")
            print(f"      {s['cuerpo'][:100]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
