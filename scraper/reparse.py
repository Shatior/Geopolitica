"""Re-extrae los artículos desde el HTML crudo ya descargado (data/raw),
sin volver a pedir nada al servidor.

    python -m scraper.reparse            # re-parsea todo
    python -m scraper.reparse --solo-genericos   # solo los mal extraídos

Reescribe data/parsed/articles.jsonl (guardando copia .bak) con los campos
recalculados por el parser actual, conservando la publicación, el número al
que pertenece y la fecha de scraping originales. Después basta con
`python -m db.load` para actualizar la base de datos.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys

from . import parse
from .config import PARSED_DIR, ROOT

log = logging.getLogger("reparse")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-parsea los HTML ya descargados")
    ap.add_argument("--solo-genericos", action="store_true",
                    help="re-parsea solo los artículos con título genérico")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    src = PARSED_DIR / "articles.jsonl"
    if not src.exists():
        log.error("No existe %s", src)
        return 2
    registros = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Si un artículo se scrapeó varias veces, nos quedamos con el último
    registros = list({r["url"]: r for r in registros}.values())
    log.info("%d artículos en el archivo", len(registros))

    shutil.copy(src, src.with_suffix(".jsonl.bak"))
    salida, cambiados, sin_html = [], 0, 0

    for rec in registros:
        generico = parse._is_generic_title(rec.get("title"))
        raw = rec.get("raw_html_path")
        path = ROOT / raw if raw else None
        if (args.solo_genericos and not generico) or not path or not path.exists():
            if not path or not path.exists():
                sin_html += 1
            salida.append(rec)
            continue

        html = path.read_text(encoding="utf-8", errors="replace")
        nuevo = parse.parse_article(html, rec["url"])
        antes = rec.get("title")
        nuevo.update({
            "publication": rec.get("publication"),
            "issue_url": rec.get("issue_url"),
            "raw_html_path": raw,
            "scraped_at": rec.get("scraped_at"),
        })
        if not nuevo["published_date"]:
            nuevo["published_date"] = rec.get("published_date")
        if nuevo["title"] != antes:
            cambiados += 1
            if cambiados <= 5:
                log.info("  %r → %r", (antes or "")[:40], nuevo["title"][:60])
        salida.append(nuevo)

    with open(src, "w", encoding="utf-8") as fh:
        for rec in salida:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    restantes = sum(1 for r in salida if parse._is_generic_title(r.get("title")))
    log.info(
        "Hecho: %d títulos corregidos · %d sin HTML local · %d siguen genéricos",
        cambiados, sin_html, restantes,
    )
    log.info("Copia de seguridad en %s. Ahora ejecuta: python -m db.load",
             src.with_suffix(".jsonl.bak").name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
