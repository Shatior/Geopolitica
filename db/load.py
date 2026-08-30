"""Carga los JSONL de data/parsed/ en la base de datos PostgreSQL de Railway.

    python -m db.load            # aplica el esquema y hace upsert de todo

Es idempotente: usa upsert por URL, así que puedes relanzarlo tras cada
sesión de scraping y solo añadirá/actualizará lo nuevo.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import PARSED_DIR, ROOT, load_config  # noqa: E402

log = logging.getLogger("db")
SCHEMA = ROOT / "db" / "schema.sql"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    if not cfg.database_url:
        log.error("Falta DATABASE_URL en .env (la URL pública de Postgres en Railway).")
        return 2

    issues = read_jsonl(PARSED_DIR / "issues.jsonl")
    articles = read_jsonl(PARSED_DIR / "articles.jsonl")
    # Si un artículo se scrapeó varias veces, gana el registro más reciente.
    articles = list({a["url"]: a for a in articles}.values())
    log.info("A cargar: %d números, %d artículos", len(issues), len(articles))

    with psycopg.connect(cfg.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA.read_text(encoding="utf-8"))

            pub_ids: dict[str, int] = {}
            for slug, pub in cfg.publications.items():
                cur.execute(
                    """INSERT INTO publications (slug, name) VALUES (%s, %s)
                       ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                       RETURNING id""",
                    (slug, pub.name),
                )
                pub_ids[slug] = cur.fetchone()["id"]

            issue_ids: dict[str, int] = {}
            for rec in issues:
                if rec["publication"] not in pub_ids:
                    continue
                cur.execute(
                    """INSERT INTO issues (publication_id, url, number, title, published_date)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (url) DO UPDATE SET
                           number = EXCLUDED.number,
                           title = EXCLUDED.title,
                           published_date = COALESCE(EXCLUDED.published_date, issues.published_date)
                       RETURNING id""",
                    (
                        pub_ids[rec["publication"]], rec["url"], rec.get("number"),
                        rec.get("title"), rec.get("published_date"),
                    ),
                )
                issue_ids[rec["url"]] = cur.fetchone()["id"]

            n = 0
            for rec in articles:
                if rec["publication"] not in pub_ids:
                    continue
                cur.execute(
                    """INSERT INTO articles
                           (publication_id, issue_id, url, title, subtitle, authors,
                            tags, published_date, body, is_full, raw_html_path, scraped_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (url) DO UPDATE SET
                           issue_id = COALESCE(EXCLUDED.issue_id, articles.issue_id),
                           title = EXCLUDED.title,
                           subtitle = EXCLUDED.subtitle,
                           authors = EXCLUDED.authors,
                           tags = EXCLUDED.tags,
                           published_date = COALESCE(EXCLUDED.published_date, articles.published_date),
                           body = EXCLUDED.body,
                           is_full = EXCLUDED.is_full,
                           raw_html_path = EXCLUDED.raw_html_path,
                           scraped_at = EXCLUDED.scraped_at""",
                    (
                        pub_ids[rec["publication"]],
                        issue_ids.get(rec.get("issue_url")),
                        rec["url"], rec.get("title"), rec.get("subtitle"),
                        rec.get("authors") or [], rec.get("tags") or [],
                        rec.get("published_date"), rec.get("body"),
                        rec.get("is_full", False), rec.get("raw_html_path"),
                        rec.get("scraped_at"),
                    ),
                )
                n += 1
                if n % 200 == 0:
                    log.info("…%d artículos", n)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.slug, count(*) AS total,
                          count(*) FILTER (WHERE a.is_full) AS completos,
                          min(a.published_date) AS desde, max(a.published_date) AS hasta
                   FROM articles a JOIN publications p ON p.id = a.publication_id
                   GROUP BY p.slug ORDER BY p.slug"""
            )
            for row in cur.fetchall():
                log.info(
                    "%s: %d artículos (%d completos), %s → %s",
                    row["slug"], row["total"], row["completos"],
                    row["desde"], row["hasta"],
                )
    log.info("Carga completada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
