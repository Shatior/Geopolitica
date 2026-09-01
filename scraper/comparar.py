"""¿Es el texto de la web el artículo entero, o solo un resumen?

    python -m scraper.comparar          # usa los PDF que ya están en data/pdfs

La pregunta decide el rumbo del proyecto. Si la web publica una versión
abreviada y el texto completo vive solo en el PDF, entonces el archivo que
tenemos en Railway es un corpus de resúmenes: sirve para ver de qué se habla,
pero no para citar al autor ni para auditar lo que afirmó.

Esta comprobación no descarga nada ni toca politicaexterior.com. Compara, para
cada número del que ya haya PDF en disco, cuánto texto tiene el PDF frente a lo
que guardamos de sus artículos. Un cociente cercano a 1 significa que la web da
la pieza íntegra; un cociente de 3 significa que nos falta dos tercios.

El PDF de un número contiene además portada, sumario, publicidad y créditos,
así que siempre medirá algo más que la suma de sus artículos. Por eso lo que
importa no es el número exacto, sino el orden de magnitud, y el informe
distingue entre «la web trae el texto» y «la web trae un resumen».
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import aviso_database_url, load_config
from .pdfs import PDF_DIR, extraer_texto


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Comparar web contra PDF")
    ap.add_argument("--limite", type=int, default=25)
    ap.add_argument("--pdf", help="ruta a un PDF suelto, descargado a mano")
    ap.add_argument("--publicacion", help="slug del PDF suelto")
    ap.add_argument("--numero", help="número del PDF suelto")
    args = ap.parse_args(argv)

    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    # Un PDF suelto basta para responder a la pregunta, y se puede descargar a
    # mano desde el navegador sin pedirle nada al sitio por programa.
    if args.pdf:
        if not (args.publicacion and args.numero):
            print("Con --pdf hay que indicar también --publicacion y --numero.")
            return 2
        sueltos = [(Path(args.pdf), args.publicacion, args.numero)]
    else:
        sueltos = []
        for ruta in sorted(PDF_DIR.glob("*.pdf")) if PDF_DIR.exists() else []:
            tallo = ruta.stem.rsplit("-", 1)
            if len(tallo) == 2:
                sueltos.append((ruta, tallo[0], tallo[1]))

    if not sueltos:
        print(f"No hay PDF en {PDF_DIR}.\n\n"
              "El rescate de texto se ejecutó en servidores temporales de GitHub, "
              "que se\nborran al terminar, así que los PDF no quedaron en ningún "
              "sitio: solo su texto,\nen la base de datos.\n\n"
              "Para responder a la pregunta basta UN número. Descárgalo a mano "
              "desde el\nnavegador y ejecuta:\n"
              "  python -m scraper.comparar --pdf ruta\\al\\numero.pdf "
              "--publicacion informe-semanal --numero 1479")
        return 1

    print("=" * 74)
    print("¿TEXTO ÍNTEGRO O RESUMEN? · web frente a PDF del número")
    print("=" * 74)
    print(f"{len(sueltos)} PDF a comparar\n")

    filas = []
    with psycopg.connect(cfg.database_url, row_factory=dict_row) as conn:
        for ruta, slug, numero in sueltos[:args.limite]:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT count(*) AS n,
                              sum(length(coalesce(a.body, ''))) AS chars
                       FROM articles a
                       JOIN issues i ON i.id = a.issue_id
                       JOIN publications p ON p.id = i.publication_id
                       WHERE p.slug = %s AND i.number = %s
                         AND a.kind <> 'portada'""",
                    (slug, numero))
                r = cur.fetchone()
            if not r or not r["n"] or not r["chars"]:
                continue
            try:
                del_pdf = len(extraer_texto(ruta))
            except Exception as exc:                      # noqa: BLE001
                print(f"  nº {numero}: no se pudo leer el PDF ({exc})")
                continue
            if del_pdf < 1000:        # escaneado o sin capa de texto
                continue
            filas.append({"slug": slug, "numero": numero, "arts": r["n"],
                          "web": r["chars"], "pdf": del_pdf,
                          "ratio": del_pdf / r["chars"]})

    if not filas:
        print("No se ha podido comparar ningún número.")
        return 1

    print(f"{'nº':>8} {'arts':>5} {'web (chars)':>12} {'pdf (chars)':>12} {'pdf/web':>8}")
    for f in sorted(filas, key=lambda d: d["ratio"]):
        print(f"{f['numero']:>8} {f['arts']:5} {f['web']:12} {f['pdf']:12} "
              f"{f['ratio']:7.1f}×")

    ratios = sorted(f["ratio"] for f in filas)
    mediana = ratios[len(ratios) // 2]
    print(f"\nMediana: el PDF tiene {mediana:.1f}× el texto que guardamos.")
    if mediana < 1.6:
        print("→ La web trae el texto íntegro. La diferencia es la portada, el\n"
              "  sumario y los créditos del número, que el PDF incluye y nosotros no.")
    elif mediana < 2.5:
        print("→ Zona ambigua. Conviene leer un artículo y su PDF en paralelo\n"
              "  antes de concluir nada.")
    else:
        print("→ La web trae un RESUMEN. El texto completo solo está en el PDF,\n"
              "  así que el archivo actual sirve para ver de qué se habla, pero no\n"
              "  para citar al autor. Habría que reextraer desde los PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
