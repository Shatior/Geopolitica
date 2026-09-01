"""Aplica el esquema y las migraciones a la base de datos, sin tocar el sitio.

    python -m db.migrar

Es idempotente y no hace ninguna petición a politicaexterior.com: solo
actualiza la estructura (columnas nuevas, índices) y las clasificaciones
derivables de los datos ya guardados, como distinguir las portadas de número
de la revista de los análisis reales.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import ROOT, aviso_database_url, load_config  # noqa: E402

log = logging.getLogger("migrar")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        log.error("%s", aviso)
        return 2

    esquema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
        cur.execute(esquema)
        conn.commit()
        cur.execute("SELECT kind, count(*) FROM articles GROUP BY kind ORDER BY kind")
        for kind, n in cur.fetchall():
            log.info("%-10s %5d registros", kind, n)
        cur.execute(
            """SELECT count(*) FROM articles
               WHERE kind <> 'portada'
                 AND (coalesce(body, '') = '' OR length(body) < 200)"""
        )
        log.info("Análisis con cuerpo muy corto: %d", cur.fetchone()[0])
    log.info("Migración aplicada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
