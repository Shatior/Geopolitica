"""Utilidad de depuración: descarga UNA página con tu sesión y muestra qué
detecta el parser (título, longitud del cuerpo, enlaces a artículos/números).
Guarda además el HTML en data/debug/ para inspeccionarlo a mano.

    python -m scraper.inspect_page https://www.politicaexterior.com/archivo/informe-semanal-ano-2023/
"""
from __future__ import annotations

import hashlib
import logging
import sys

from . import parse
from .config import DEBUG_DIR, ensure_dirs, load_config
from .session import PoliteSession


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ensure_dirs()
    cfg = load_config()
    sess = PoliteSession(cfg.base_url, cfg.throttle)
    if cfg.cookies_file:
        try:
            sess.load_cookies(cfg.cookies_file)
        except FileNotFoundError as exc:
            print(f"AVISO: {exc}\nContinuando sin autenticar…")
    elif cfg.username and cfg.password:
        sess.login(cfg.username, cfg.password)

    resp = sess.get(url)
    print(f"\nHTTP {resp.status_code}  ({len(resp.text)} bytes)")
    out = DEBUG_DIR / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".html")
    out.write_text(resp.text, encoding="utf-8")
    print(f"HTML guardado en {out}\n")
    print(f"Sesión autenticada: {sess.is_logged_in()}\n")

    art = parse.parse_article(resp.text, url)
    print(f"Título:  {art['title']}")
    print(f"Autores: {art['authors']}")
    print(f"Fecha:   {art['published_date']}")
    print(f"Cuerpo:  {len(art['body'])} caracteres  (completo: {art['is_full']})")
    if art["body"]:
        print(f"Inicio:  {art['body'][:300]}…\n")

    articles = parse.extract_article_links(
        resp.text, cfg.base_url, cfg.article_url_patterns, cfg.exclude_path_prefixes
    )
    print(f"Enlaces a artículos detectados: {len(articles)}")
    for u in articles[:15]:
        print(f"  {u}")

    for slug, pub in cfg.publications.items():
        issues = parse.extract_issue_links(
            resp.text, cfg.base_url, pub.archive_slug, pub.issue_url_hints
        )
        if issues:
            print(f"\nEnlaces a números de {slug}: {len(issues)}")
            for u in issues[:15]:
                print(f"  {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
