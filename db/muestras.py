"""Textos de ejemplo por año, para mirar con los ojos lo que las lentes cuentan.

    python -m db.muestras --publicacion informe-semanal --anyos 2019,2023,2026

Cuando una lente señala como novedad palabras banales («mediante», «mantiene»,
«progresivamente»), la causa no suele estar en la agenda internacional sino en
los propios textos: un cambio de formato, de redacción o de extracción. Esta
herramienta es la comprobación más barata y más concluyente: leer un trozo.

Solo consulta la base de datos; no toca politicaexterior.com.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import aviso_database_url, load_config  # noqa: E402

MARCADORES = ["mediante", "mantiene", "progresivamente", "plenamente",
              "unicamente", "estructural", "eventual", "reforzar"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Muestras de texto por año")
    ap.add_argument("--publicacion", default="informe-semanal")
    ap.add_argument("--anyos", default="2019,2023,2026")
    ap.add_argument("--por-anyo", type=int, default=2)
    ap.add_argument("--chars", type=int, default=600)
    args = ap.parse_args(argv)

    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    anyos = [int(a) for a in args.anyos.split(",") if a.strip()]

    with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
        print("=" * 74)
        print(f"MUESTRAS DE TEXTO · {args.publicacion}")
        print("=" * 74)

        for anyo in anyos:
            cur.execute("""
                SELECT count(*),
                       avg(length(coalesce(a.body,'')))::int,
                       avg(array_length(regexp_split_to_array(
                           coalesce(a.body,''), E'\\\\s+'), 1))::int
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                  AND EXTRACT(YEAR FROM a.published_date) = %s
            """, (args.publicacion, anyo))
            n, chars, palabras = cur.fetchone()
            print(f"\n{'─' * 74}\n{anyo}: {n} análisis · {chars or 0} caracteres "
                  f"de media · {palabras or 0} palabras")

            # ¿Cuántos usan las palabras que las lentes señalan como novedad?
            marcas = []
            for m in MARCADORES:
                cur.execute("""
                    SELECT count(*) FROM articles a
                    JOIN publications p ON p.id = a.publication_id
                    WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                      AND EXTRACT(YEAR FROM a.published_date) = %s
                      AND a.body ILIKE %s
                """, (args.publicacion, anyo, f"%{m}%"))
                marcas.append(f"{m} {cur.fetchone()[0] * 100 // max(n, 1)}%")
            print("  presencia: " + " · ".join(marcas))

            # ¿Escriben los mismos? Un cambio de firmas explica un cambio de
            # registro mucho mejor que cualquier hipótesis sobre la agenda.
            cur.execute("""
                SELECT count(*) FILTER (WHERE a.author IS NOT NULL),
                       count(DISTINCT a.author)
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                  AND EXTRACT(YEAR FROM a.published_date) = %s
            """, (args.publicacion, anyo))
            con_firma, firmas = cur.fetchone()
            cur.execute("""
                SELECT count(*) FROM articles a
                JOIN publications p ON p.id = a.publication_id
                WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                  AND EXTRACT(YEAR FROM a.published_date) = %s
                  AND a.author IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM articles b
                      JOIN publications q ON q.id = b.publication_id
                      WHERE q.slug = p.slug AND b.author = a.author
                        AND EXTRACT(YEAR FROM b.published_date) < %s)
            """, (args.publicacion, anyo, anyo))
            nuevos = cur.fetchone()[0]
            print(f"  firmas: {firmas} distintas en {con_firma} análisis firmados"
                  f"  ·  {nuevos} de autores que no habían publicado antes "
                  f"({nuevos * 100 // max(con_firma, 1)}%)")

            cur.execute("""
                SELECT a.author, count(*) c
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                  AND EXTRACT(YEAR FROM a.published_date) = %s
                  AND a.author IS NOT NULL
                GROUP BY a.author ORDER BY c DESC LIMIT 6
            """, (args.publicacion, anyo))
            top = ", ".join(f"{au} ({c})" for au, c in cur.fetchall())
            if top:
                print(f"  más frecuentes: {top}")

            cur.execute("""
                SELECT a.published_date, a.title, a.body, a.url
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE p.slug = %s AND a.kind <> 'portada' AND a.is_full
                  AND EXTRACT(YEAR FROM a.published_date) = %s
                ORDER BY a.published_date
                LIMIT %s
            """, (args.publicacion, anyo, args.por_anyo))
            for fecha, titulo, body, url in cur.fetchall():
                cuerpo = " ".join((body or "").split())
                print(f"\n  · {fecha} — {titulo}")
                print(f"    {url}")
                print(f"    «{cuerpo[:args.chars]}…»")
    return 0


if __name__ == "__main__":
    sys.exit(main())
