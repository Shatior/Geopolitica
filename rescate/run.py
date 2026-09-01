"""Rescatar del PDF las secciones que nunca estuvieron en la web.

    python -m rescate.run --probar data/pdfs/informe-semanal-870.pdf   # sin BD
    python -m rescate.run --simular          # dice qué haría, no escribe
    python -m rescate.run                    # carga en Railway

Hasta 2020 el sitio publicaba una sola sección de cada informe. Del número
870, de siete secciones y 21.659 caracteres de análisis, la web dio 3.259: el
15%. Las otras seis existen únicamente dentro del PDF.

De cada número ya tenemos una pieza, y esa pieza es una de las secciones del
PDF. No se toca: conserva su URL en el sitio, sus etiquetas y el
enriquecimiento que ya se pagó. Lo único que se le corrige es el titular,
porque el que guardamos («#ISPE 870. 16 diciembre 2013») es el del número, no
el del texto («Crimen –casi– sin castigo»). Las secciones restantes entran
como piezas nuevas del mismo número.

El emparejamiento se hace por el arranque del texto, comparando sin acentos ni
puntuación: una sección y su gemela de la web empiezan con las mismas
palabras. Si no casa ninguna, la sección entra como nueva; si casan dos, no se
toca ninguna y se avisa, porque duplicar es peor que quedarse corto.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rescate.secciones import partir  # noqa: E402
from scraper.config import aviso_database_url, load_config  # noqa: E402
from scraper.pdfs import PDF_DIR  # noqa: E402

log = logging.getLogger("rescate")

# Caracteres de arranque que se comparan para emparejar. Suficiente para
# distinguir siete secciones entre sí, corto para aguantar erratas.
HUELLA = 90

# Un titular es del número y no del texto cuando, quitado el «#ISPE 870»,
# no queda nada o queda solo una fecha. «#ISPE 1162: Sísifo en Buenos Aires»
# sí lleva título propio y no se toca.
_PREFIJO = re.compile(
    r"^\s*#?\s*(?:ispe|informe semanal(?: de pol[íi]tica exterior)?)"
    r"\s*n?[.\u00ba\u00b0]?\s*\d*", re.I)
_SOLO_FECHA = re.compile(
    r"^[\s.:\u00b7,\-\u2013]*(?:\d{1,2}\s*)?(?:de\s+)?"
    r"(?:ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[a-z]*\.?"
    r"\s*(?:de\s+)?\d{0,4}[\s.]*$", re.I)


def GENERICO(titulo: str | None):   # noqa: N802  (se usa como un patrón)
    """Verdadero si el titular guardado nombra el número y no el texto."""
    resto = _PREFIJO.sub("", titulo or "", count=1)
    if resto == (titulo or ""):
        return None                 # no lleva el prefijo: es un titular propio
    return not resto.strip(" .:\u00b7,-\u2013") or _SOLO_FECHA.match(resto)


def _plano(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def emparejar(secciones: list[dict], articulos: list[dict]) -> dict[int, int]:
    """De índice de sección a id de artículo ya guardado."""
    pares: dict[int, int] = {}
    for i, sec in enumerate(secciones):
        huella = _plano(sec["cuerpo"])[:HUELLA]
        if not huella:
            continue
        casan = [a for a in articulos
                 if _plano(a["body"])[:HUELLA] == huella]
        if len(casan) == 1:
            pares[i] = casan[0]["id"]
        elif len(casan) > 1:
            log.warning("  %d artículos casan con «%s»; no toco ninguno",
                        len(casan), sec["titulo"][:50])
    return pares


def numero_de(ruta: Path, publicacion: str) -> str | None:
    m = re.fullmatch(rf"{re.escape(publicacion)}-(.+)", ruta.stem)
    return m.group(1) if m else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rescatar secciones del PDF")
    ap.add_argument("--probar", help="parte un PDF y lo enseña, sin base de datos")
    ap.add_argument("--publicacion", default="informe-semanal")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--simular", action="store_true",
                    help="dice qué haría sin escribir nada")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.probar:
        from rescate.secciones import main as ver
        return ver([args.probar])

    import psycopg
    from psycopg.rows import dict_row

    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    pdfs = sorted(PDF_DIR.glob(f"{args.publicacion}-*.pdf"))
    if not pdfs:
        print(f"No hay PDF de {args.publicacion} en {PDF_DIR}.")
        print("Descárgalos primero:  python -m scraper.pdfs --from-db "
              f"--publication {args.publicacion}")
        return 2
    log.info("%d PDF en disco", len(pdfs))

    nuevas = corregidos = sin_numero = ya_estaban = 0
    with psycopg.connect(cfg.database_url, row_factory=dict_row) as conn, \
            conn.cursor() as cur:
        cur.execute("SELECT id FROM publications WHERE slug = %s",
                    (args.publicacion,))
        fila = cur.fetchone()
        if not fila:
            print(f"Publicación desconocida: {args.publicacion}")
            return 2
        pub_id = fila["id"]

        cur.execute("""SELECT id, number, url, published_date FROM issues
                        WHERE publication_id = %s AND number IS NOT NULL""",
                    (pub_id,))
        numeros = {str(r["number"]): r for r in cur.fetchall()}

        for n, ruta in enumerate(pdfs, 1):
            num = numero_de(ruta, args.publicacion)
            issue = numeros.get(num or "")
            if issue is None:
                log.warning("%s: sin número reconocible en la base", ruta.name)
                sin_numero += 1
                continue

            secciones = partir(ruta)
            if not secciones:
                log.warning("%s: 0 secciones; maqueta distinta, lo dejo",
                            ruta.name)
                continue

            cur.execute("""SELECT id, title, body FROM articles
                            WHERE issue_id = %s""", (issue["id"],))
            articulos = cur.fetchall()
            pares = emparejar(secciones, articulos)
            fecha = issue["published_date"]
            if fecha is None and articulos:
                cur.execute("""SELECT max(published_date) AS f FROM articles
                                WHERE issue_id = %s""", (issue["id"],))
                fecha = cur.fetchone()["f"]

            for i, sec in enumerate(secciones):
                if i in pares:
                    art = next(a for a in articulos if a["id"] == pares[i])
                    if GENERICO(art["title"]):
                        log.info("  nº %s · corrijo titular: «%s» → «%s»",
                                 num, (art["title"] or "")[:34], sec["titulo"][:44])
                        if not args.simular:
                            cur.execute(
                                """UPDATE articles SET title = %s,
                                       subtitle = COALESCE(NULLIF(%s, ''), subtitle)
                                   WHERE id = %s""",
                                (sec["titulo"], sec["antetitulo"], art["id"]))
                        corregidos += 1
                    else:
                        ya_estaban += 1
                    continue

                url = f"{issue['url']}#seccion-{sec['orden']}"
                if not args.simular:
                    cur.execute(
                        """INSERT INTO articles
                               (publication_id, issue_id, url, title, subtitle,
                                authors, tags, published_date, body, is_full,
                                scraped_at, kind)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true,
                                   now(), 'seccion-pdf')
                           ON CONFLICT (url) DO UPDATE SET
                               title = EXCLUDED.title,
                               subtitle = EXCLUDED.subtitle,
                               body = EXCLUDED.body""",
                        (pub_id, issue["id"], url, sec["titulo"],
                         sec["antetitulo"], [], [], fecha, sec["cuerpo"]))
                nuevas += 1

            if not args.simular:
                conn.commit()
            if args.limite and n >= args.limite:
                break

    print()
    print(f"  secciones nuevas      {nuevas}")
    print(f"  titulares corregidos  {corregidos}")
    print(f"  ya estaban bien       {ya_estaban}")
    print(f"  PDF sin número        {sin_numero}")
    if args.simular:
        print("\n  (simulación: no se ha escrito nada)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
