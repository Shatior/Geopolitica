"""Extracción de enlaces y contenido de las páginas de politicaexterior.com.

El sitio es un WordPress, así que la extracción combina tres fuentes en orden
de fiabilidad: JSON-LD, metadatos OpenGraph y selectores CSS habituales de
temas de WordPress. Si el tema cambia, ajusta BODY_SELECTORS o usa
`python -m scraper.inspect_page <url>` para ver qué está detectando.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BODY_SELECTORS = [
    "div.entry-content",
    "div.post-content",
    "div.article-content",
    "section.article-body",
    "article .content",
    "article",
    "main",
]

PAYWALL_MARKERS = [
    "suscríbete para seguir leyendo",
    "contenido exclusivo para suscriptores",
    "para seguir leyendo",
    "inicia sesión para continuar",
    "hazte suscriptor",
]

# Longitud mínima (caracteres) para considerar que tenemos el cuerpo completo.
MIN_FULL_BODY = 1200


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _same_site_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    host = urlparse(base_url).netloc
    seen: list[str] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url + "/", a["href"]).split("#")[0].split("?")[0]
        p = urlparse(url)
        if p.netloc != host:
            continue
        if url not in seen:
            seen.append(url)
    return seen


def extract_issue_links(
    html: str, base_url: str, archive_slug: str, hints: list[str]
) -> list[str]:
    """Enlaces a números concretos desde una página de archivo anual.

    Un enlace cuenta como número si su path contiene alguno de los `hints`
    (p. ej. /revista-informe/) o si termina en `<archive_slug>-<número>/`.
    """
    tail_re = re.compile(rf"{re.escape(archive_slug)}-\d+/?$")
    out = []
    for url in _same_site_links(soup_of(html), base_url):
        path = urlparse(url).path
        if any(h in path for h in hints) or tail_re.search(path):
            if "/archivo/" not in path and "/ultimo/" not in path:
                out.append(url)
    return out


def extract_article_links(
    html: str,
    base_url: str,
    article_patterns: list[str],
    exclude_prefixes: list[str],
) -> list[str]:
    """Enlaces a artículos individuales desde la página de un número o de una
    página de categoría."""
    pats = [re.compile(p) for p in article_patterns]
    out = []
    for url in _same_site_links(soup_of(html), base_url):
        path = urlparse(url).path
        if path in ("", "/"):
            continue
        if any(path.startswith(pref) for pref in exclude_prefixes):
            continue
        if any(p.search(path) for p in pats):
            out.append(url)
    return out


def find_next_page(html: str, base_url: str) -> str | None:
    """Paginación WordPress: <link rel=next>, a.next, o /page/N/."""
    soup = soup_of(html)
    link = soup.find("link", rel="next")
    if link and link.get("href"):
        return urljoin(base_url + "/", link["href"])
    a = soup.select_one("a.next, .nav-links a.next, a[rel=next]")
    if a and a.get("href"):
        return urljoin(base_url + "/", a["href"])
    return None


# ---------------------------------------------------------------- artículos

def _json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and "@graph" in item:
                candidates.extend(
                    g for g in item["@graph"] if isinstance(g, dict)
                )
            if isinstance(item, dict) and item.get("@type") in (
                "Article", "NewsArticle", "BlogPosting",
            ):
                return item
    return {}


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    return tag.get("content") if tag and tag.get("content") else None


def _best_body(soup: BeautifulSoup) -> str:
    best = ""
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if not node:
            continue
        for junk in node.select(
            "script, style, nav, form, .related, .share, .social, "
            ".newsletter, .paywall, aside, footer, header"
        ):
            junk.decompose()
        paragraphs = [
            p.get_text(" ", strip=True)
            for p in node.find_all(["p", "h2", "h3", "blockquote", "li"])
        ]
        text = "\n\n".join(t for t in paragraphs if t)
        if not text:
            text = node.get_text(" ", strip=True)
        if len(text) > len(best):
            best = text
    return best


def parse_article(html: str, url: str) -> dict:
    soup = soup_of(html)
    ld = _json_ld(soup)

    title = (
        ld.get("headline")
        or _meta(soup, "og:title")
        or (soup.h1.get_text(strip=True) if soup.h1 else None)
        or (soup.title.get_text(strip=True) if soup.title else url)
    )

    authors: list[str] = []
    ld_author = ld.get("author")
    if isinstance(ld_author, dict):
        ld_author = [ld_author]
    if isinstance(ld_author, list):
        authors = [a.get("name") for a in ld_author if isinstance(a, dict) and a.get("name")]
    if not authors:
        authors = sorted(
            {
                a.get_text(strip=True)
                for a in soup.select("a[href*='/autores/']")
                if a.get_text(strip=True)
            }
        )

    published = (
        ld.get("datePublished")
        or _meta(soup, "article:published_time")
        or (soup.find("time", datetime=True) or {}).get("datetime")
    )

    tags = [
        m["content"]
        for m in soup.find_all("meta", attrs={"property": "article:tag"})
        if m.get("content")
    ]
    if not tags:
        tags = sorted(
            {t.get_text(strip=True) for t in soup.select("a[rel=tag]") if t.get_text(strip=True)}
        )

    subtitle = _meta(soup, "og:description")
    body = _best_body(soup)

    lower = (body or "").lower()
    paywalled = len(body) < MIN_FULL_BODY or any(m in lower for m in PAYWALL_MARKERS)

    return {
        "url": url,
        "title": title,
        "subtitle": subtitle,
        "authors": authors,
        "published_date": (published or "")[:10] or None,
        "tags": tags,
        "body": body,
        "is_full": not paywalled,
    }


def parse_issue(html: str, url: str) -> dict:
    soup = soup_of(html)
    m = re.search(r"-(\d+)/?$", urlparse(url).path)
    title = (
        _meta(soup, "og:title")
        or (soup.h1.get_text(strip=True) if soup.h1 else None)
        or url
    )
    published = _meta(soup, "article:published_time") or (
        (soup.find("time", datetime=True) or {}).get("datetime")
    )
    return {
        "url": url,
        "number": int(m.group(1)) if m else None,
        "title": title,
        "published_date": (published or "")[:10] or None,
    }
