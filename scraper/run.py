"""Orquestador del scraping.

Uso típico (desde la raíz del repo, con .env configurado):

    # Prueba pequeña: un año de una publicación, máximo 5 artículos
    python -m scraper.run --publication informe-semanal --years 2023 --limit 5

    # Todo el Informe Semanal
    python -m scraper.run --publication informe-semanal

    # Todo (semanal + revista bimestral). Tardará horas: es intencionado.
    python -m scraper.run

Es reanudable: el progreso se guarda en data/state.json y los resultados en
data/parsed/*.jsonl; si lo cortas (Ctrl+C) o el servidor nos frena, al volver
a lanzarlo continúa donde lo dejó sin repetir peticiones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, datetime, timezone

from . import parse
from .config import (
    PARSED_DIR, RAW_DIR, STATE_FILE, ensure_dirs, load_config,
)
from .session import PoliteSession, ScraperBlocked

log = logging.getLogger("scraper")


# ------------------------------------------------------------------ estado
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"done": {}, "issues": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def save_raw(url: str, html: str) -> str:
    name = hashlib.sha1(url.encode()).hexdigest() + ".html"
    path = RAW_DIR / name
    path.write_text(html, encoding="utf-8")
    return f"data/raw/{name}"


def append_jsonl(filename: str, record: dict) -> None:
    with open(PARSED_DIR / filename, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetch_done_from_db(database_url: str) -> tuple[set[str], set[str]]:
    """URLs ya cargadas en la base de datos, para ejecuciones sin estado
    local (GitHub Actions). Un número se considera hecho si ya tiene al
    menos 3 artículos cargados (los informes traen ~5; el umbral evita dar
    por completo un número a medio scrapear)."""
    import psycopg

    articles: set[str] = set()
    issues: set[str] = set()
    try:
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT url FROM articles")
            articles = {r[0] for r in cur.fetchall()}
            cur.execute(
                """SELECT i.url FROM issues i
                   WHERE (SELECT count(*) FROM articles a
                          WHERE a.issue_id = i.id) >= 3"""
            )
            issues = {r[0] for r in cur.fetchall()}
    except psycopg.errors.UndefinedTable:
        pass  # base de datos aún vacía
    return articles, issues


# ------------------------------------------------------------------ fases
def discover_issues(cfg, sess, pub, years) -> list[str]:
    """Recorre los archivos anuales y devuelve las URLs de números."""
    issue_urls: list[str] = []
    for year in years:
        url = f"{cfg.base_url}/archivo/{pub.archive_slug}-ano-{year}/"
        while url:
            resp = sess.get(url)
            if resp.status_code == 404:
                log.info("Sin archivo para %s %s (404), se omite", pub.slug, year)
                break
            resp.raise_for_status()
            found = parse.extract_issue_links(
                resp.text, cfg.base_url, pub.archive_slug, pub.issue_url_hints
            )
            log.info("%s %s: %d números en %s", pub.slug, year, len(found), url)
            issue_urls.extend(u for u in found if u not in issue_urls)
            # Solo seguimos la paginación si sigue dentro del archivo; el tema
            # de WordPress a veces enlaza como "siguiente" archivos por fecha
            # genéricos (/2023/page/2/) que no son nuestros.
            next_url = parse.find_next_page(resp.text, cfg.base_url)
            url = next_url if next_url and "/archivo/" in next_url else None
    return issue_urls


def discover_articles_via_category(cfg, sess, pub, max_pages=200) -> list[str]:
    """Descubrimiento alternativo: pagina /categoria-articulo/<slug>/page/N/."""
    article_urls: list[str] = []
    for n in range(1, max_pages + 1):
        suffix = "" if n == 1 else f"page/{n}/"
        url = f"{cfg.base_url}/categoria-articulo/{pub.category_slug}/{suffix}"
        resp = sess.get(url)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        found = parse.extract_article_links(
            resp.text, cfg.base_url, cfg.article_url_patterns,
            cfg.exclude_path_prefixes,
        )
        if not found:
            break
        new = [u for u in found if u not in article_urls]
        log.info("Categoría %s página %d: %d artículos", pub.category_slug, n, len(new))
        article_urls.extend(new)
    return article_urls


def scrape_article(cfg, sess, state, url, pub_slug, issue_url=None,
                   issue_date=None) -> bool:
    resp = sess.get(url)
    if resp.status_code == 404:
        state["done"][url] = {"status": "404"}
        return False
    resp.raise_for_status()
    record = parse.parse_article(resp.text, url)
    record["publication"] = pub_slug
    record["issue_url"] = issue_url
    if not record["published_date"] and issue_date:
        record["published_date"] = issue_date
    record["raw_html_path"] = save_raw(url, resp.text)
    record["scraped_at"] = datetime.now(timezone.utc).isoformat()
    append_jsonl("articles.jsonl", record)
    state["done"][url] = {
        "status": "ok" if record["is_full"] else "paywalled",
    }
    if not record["is_full"]:
        log.warning("Contenido truncado/paywall en %s", url)
    return record["is_full"]


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scraper de politicaexterior.com")
    ap.add_argument("--publication", action="append",
                    help="slug de config.yaml (repetible); por defecto, todas")
    ap.add_argument("--years", help="p. ej. 2023 o 2019-2024; por defecto, "
                    "desde first_year hasta el año actual")
    ap.add_argument("--limit", type=int, default=0,
                    help="máximo de artículos nuevos en esta ejecución (0 = sin límite)")
    ap.add_argument("--discover", choices=["archive", "category"],
                    default="archive",
                    help="archive: años→números→artículos (con relación número-artículo); "
                         "category: pagina la categoría de artículos directamente")
    ap.add_argument("--skip-from-db", action="store_true",
                    help="consulta DATABASE_URL y omite números/artículos ya "
                         "cargados (para ejecuciones sin estado local, p. ej. CI)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    ensure_dirs()
    cfg = load_config()
    state = load_state()

    pubs = args.publication or list(cfg.publications)
    for slug in pubs:
        if slug not in cfg.publications:
            log.error("Publicación desconocida: %s (mira config.yaml)", slug)
            return 2

    db_articles: set[str] = set()
    db_issues: set[str] = set()
    if args.skip_from_db:
        if not cfg.database_url:
            log.error("--skip-from-db requiere DATABASE_URL en el entorno o .env")
            return 2
        db_articles, db_issues = fetch_done_from_db(cfg.database_url)
        log.info(
            "BD: %d artículos y %d números ya cargados, se omitirán",
            len(db_articles), len(db_issues),
        )

    sess = PoliteSession(cfg.base_url, cfg.throttle)
    if cfg.cookies_file:
        sess.load_cookies(cfg.cookies_file)
    elif cfg.username and cfg.password:
        sess.login(cfg.username, cfg.password)
    else:
        log.warning(
            "Sin autenticación configurada (.env): solo se obtendrán los "
            "extractos públicos, no el texto completo."
        )

    n_new = 0
    paywalled_streak = 0
    try:
        for slug in pubs:
            pub = cfg.publications[slug]
            if args.years:
                lo, _, hi = args.years.partition("-")
                years = range(int(lo), int(hi or lo) + 1)
            else:
                years = range(pub.first_year, date.today().year + 1)

            if args.discover == "category":
                targets = [
                    (u, None, None)
                    for u in discover_articles_via_category(cfg, sess, pub)
                ]
            else:
                targets = []
                for issue_url in discover_issues(cfg, sess, pub, years):
                    if issue_url in db_issues:
                        continue
                    if issue_url in state["issues"]:
                        cached = state["issues"][issue_url]
                        arts = cached["articles"]
                        issue_date = cached.get("published_date")
                    else:
                        resp = sess.get(issue_url)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                        issue_rec = parse.parse_issue(resp.text, issue_url)
                        issue_rec["publication"] = slug
                        arts = parse.extract_article_links(
                            resp.text, cfg.base_url, cfg.article_url_patterns,
                            cfg.exclude_path_prefixes,
                        )
                        issue_rec["article_urls"] = arts
                        issue_date = issue_rec["published_date"]
                        append_jsonl("issues.jsonl", issue_rec)
                        state["issues"][issue_url] = {
                            "articles": arts, "published_date": issue_date,
                        }
                        save_state(state)
                        if not arts:
                            log.warning(
                                "0 artículos detectados en %s — si es general, "
                                "revisa article_url_patterns en config.yaml "
                                "con: python -m scraper.inspect_page %s",
                                issue_url, issue_url,
                            )
                    targets.extend((u, issue_url, issue_date) for u in arts)

            for url, issue_url, issue_date in targets:
                if url in state["done"] or url in db_articles:
                    continue
                full = scrape_article(
                    cfg, sess, state, url, slug, issue_url, issue_date
                )
                n_new += 1
                paywalled_streak = 0 if full else paywalled_streak + 1
                if n_new % 10 == 0:
                    save_state(state)
                if paywalled_streak >= 5:
                    log.error(
                        "5 artículos seguidos truncados: la sesión no está "
                        "autenticada o ha caducado. Renueva las cookies "
                        "(data/cookies.txt) y vuelve a lanzar; lo ya "
                        "descargado no se repite."
                    )
                    return 3
                if args.limit and n_new >= args.limit:
                    log.info("Alcanzado --limit=%d", args.limit)
                    save_state(state)
                    return 0
    except ScraperBlocked as exc:
        log.error("%s", exc)
        return 4
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario; progreso guardado.")
    finally:
        save_state(state)

    log.info("Hecho: %d artículos nuevos en esta ejecución.", n_new)
    return 0


if __name__ == "__main__":
    sys.exit(run())
