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


MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_RE_FECHA_ES = re.compile(
    r"(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})", re.IGNORECASE
)


def spanish_date(text: str | None) -> str | None:
    """'25 de diciembre de 2023' -> '2023-12-25'."""
    if not text:
        return None
    m = _RE_FECHA_ES.search(text)
    if not m:
        return None
    return f"{int(m.group(3)):04d}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


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
        # El tema del sitio genera a veces hrefs con doble barra (//ultimo/...)
        url = p._replace(path=re.sub(r"/{2,}", "/", p.path)).geturl()
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
    """Nodo JSON-LD más informativo. Yoast (el SEO del sitio) suele publicar
    un @graph cuyo nodo con datePublished es de tipo WebPage, no Article."""
    fallback: dict = {}
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
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("Article", "NewsArticle", "BlogPosting"):
                return item
            if not fallback and item.get("datePublished"):
                fallback = item
    return fallback


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
    title = re.sub(r"\s*\|\s*Política Exterior\s*$", "", title)

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
    if not published:
        node = soup.select_one(
            ".ctaSubscriptionsTitle, [class*='date'], [class*='fecha']"
        )
        published = spanish_date(node.get_text(" ", strip=True) if node else None)

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
    number = int(m.group(1)) if m else None

    # El sitio pone varios h1.title_cat y el primero puede estar vacío
    title = next(
        (t for h in soup.find_all("h1") if (t := h.get_text(strip=True))), None
    )
    if title and number:
        title = f"{title} {number}"

    # La fecha del número está en el bloque de suscripción/portada
    node = soup.select_one(".ctaSubscriptionsTitle")
    published = spanish_date(node.get_text(" ", strip=True) if node else None)
    if not published:
        published = (
            _meta(soup, "article:published_time")
            or (soup.find("time", datetime=True) or {}).get("datetime")
            or ""
        )[:10] or None

    pdf = soup.select_one("a[href*='download.php']")
    pdf_url = urljoin(url, pdf["href"]) if pdf and pdf.get("href") else None

    return {
        "url": url,
        "number": number,
        "title": title or url,
        "published_date": published,
        "pdf_url": pdf_url,
    }
