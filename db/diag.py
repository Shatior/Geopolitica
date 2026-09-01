"""Diagnóstico de artículos con extracción genérica ('Política Exterior').

    python -m db.diag

Consulta la base de datos, cuenta los artículos mal extraídos y examina sus
HTML crudos locales (data/raw) para distinguir entre: (a) el titular real
está en el HTML pero el parser no lo cogió, o (b) el servidor sirvió otra
página (portada/login) y hay que re-descargar.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import ROOT, load_config  # noqa: E402

GENERIC_TITLE = "Política Exterior"
GENERIC_SUB = "Estudios de Política Exterior%"


def main() -> int:
    cfg = load_config()
    if not cfg.database_url:
        print("Falta DATABASE_URL en .env")
        return 2
    with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        total = cur.fetchone()[0]
        cur.execute(
            """SELECT count(*) FROM articles
               WHERE title = %s OR subtitle LIKE %s""",
            (GENERIC_TITLE, GENERIC_SUB),
        )
        bad = cur.fetchone()[0]
        print(f"Artículos totales: {total}  ·  con extracción genérica: {bad}")

        cur.execute(
            """SELECT EXTRACT(YEAR FROM published_date)::int AS y, count(*)
               FROM articles WHERE title = %s OR subtitle LIKE %s
               GROUP BY y ORDER BY y""",
            (GENERIC_TITLE, GENERIC_SUB),
        )
        print("Por año:", ", ".join(f"{y}: {n}" for y, n in cur.fetchall()))

        cur.execute(
            """SELECT url, raw_html_path, length(coalesce(body,'')) AS blen,
                      left(coalesce(body,''), 90) AS binicio
               FROM articles WHERE title = %s OR subtitle LIKE %s
               ORDER BY published_date DESC NULLS LAST LIMIT 6""",
            (GENERIC_TITLE, GENERIC_SUB),
        )
        rows = cur.fetchall()

    print("\n--- Muestra (los más recientes) ---")
    for url, raw_path, blen, binicio in rows:
        print(f"\nURL: {url}")
        print(f"  cuerpo: {blen} chars · empieza: {binicio!r}")
        path = ROOT / raw_path if raw_path else None
        if not path or not path.exists():
            print("  HTML crudo: NO disponible en este equipo")
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        slug_words = [w for w in url.rstrip("/").rsplit("/", 1)[-1].split("-")
                      if len(w) > 4 and not w.isdigit()][:3]
        og = soup.find("meta", attrs={"property": "og:title"})
        canon = soup.find("link", rel="canonical")
        h1s = [h.get_text(" ", strip=True)[:80] for h in soup.find_all("h1")[:3]]
        h2s = [h.get_text(" ", strip=True)[:80] for h in soup.find_all("h2")[:4]]
        print(f"  HTML crudo: {raw_path} ({len(html)} bytes)")
        print(f"  <title>: {(soup.title.get_text(strip=True) if soup.title else '')[:90]}")
        print(f"  og:title: {(og.get('content') if og else '')[:90]}")
        print(f"  canonical: {(canon.get('href') if canon else '')[:110]}")
        print(f"  h1: {h1s}")
        print(f"  h2: {h2s}")
        found = sum(1 for w in slug_words if w.lower() in html.lower())
        print(f"  palabras del slug {slug_words} presentes en el HTML: {found}/{len(slug_words)}")
    print(
        "\nInterpretación: si las palabras del slug están presentes y hay un "
        "h1/h2 con el titular real → es arreglable re-parseando (sin volver a "
        "descargar). Si el canonical apunta a la portada o el slug no aparece "
        "→ el servidor sirvió otra página y habrá que re-descargar esas URLs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
