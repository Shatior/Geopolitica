"""Frontend de consulta de la hemeroteca.

Ejecución local:
    uvicorn web.app:app --reload
    # abre http://127.0.0.1:8000  (usuario/contraseña: los de WEB_USER/WEB_PASSWORD)

Lee DATABASE_URL (y opcionalmente WEB_USER/WEB_PASSWORD para proteger el
acceso) del entorno o del .env. Si WEB_PASSWORD no está definida, la app se
sirve sin autenticación: hazlo solo en local, nunca desplegada.
"""
from __future__ import annotations

import os
import secrets as pysecrets
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD")
PER_PAGE = 25

app = FastAPI(title="Geopolítica", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not WEB_PASSWORD:
        return
    ok = (
        creds is not None
        and pysecrets.compare_digest(creds.username.encode(), WEB_USER.encode())
        and pysecrets.compare_digest(creds.password.encode(), WEB_PASSWORD.encode())
    )
    if not ok:
        raise HTTPException(401, "No autorizado",
                            headers={"WWW-Authenticate": "Basic realm=Geopolitica"})


def db() -> psycopg.Connection:
    if not DATABASE_URL:
        raise HTTPException(500, "Falta DATABASE_URL en el entorno o .env")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = "",
    pub: str = "",
    year: int = 0,
    page: int = 1,
    _: None = Depends(require_auth),
):
    q, pub = q.strip(), pub.strip()
    page = max(1, page)
    where = ["TRUE"]
    params: dict = {"q": q, "pub": pub, "year": year}
    if q:
        where.append("a.tsv @@ websearch_to_tsquery('spanish', %(q)s)")
    if pub:
        where.append("p.slug = %(pub)s")
    if year:
        where.append("EXTRACT(YEAR FROM a.published_date) = %(year)s")
    where_sql = " AND ".join(where)

    snippet_sql = (
        """ts_headline('spanish', a.body, websearch_to_tsquery('spanish', %(q)s),
                       'MaxFragments=2, MaxWords=30, MinWords=15,
                        StartSel=<mark>, StopSel=</mark>')"""
        if q else "coalesce(a.subtitle, left(a.body, 260))"
    )
    order_sql = (
        "ts_rank(a.tsv, websearch_to_tsquery('spanish', %(q)s)) DESC,"
        if q else ""
    )

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) AS n FROM articles a
                JOIN publications p ON p.id = a.publication_id
                WHERE {where_sql}""",
            params,
        )
        total = cur.fetchone()["n"]

        cur.execute(
            f"""SELECT a.id, a.title, a.published_date, a.authors, a.tags,
                       a.is_full, p.slug AS pub_slug, p.name AS pub_name,
                       {snippet_sql} AS snippet
                FROM articles a
                JOIN publications p ON p.id = a.publication_id
                WHERE {where_sql}
                ORDER BY {order_sql} a.published_date DESC NULLS LAST, a.id DESC
                LIMIT %(limit)s OFFSET %(offset)s""",
            {**params, "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE},
        )
        articles = cur.fetchall()

        cur.execute("SELECT slug, name FROM publications ORDER BY slug")
        pubs = cur.fetchall()
        cur.execute(
            """SELECT DISTINCT EXTRACT(YEAR FROM published_date)::int AS y
               FROM articles WHERE published_date IS NOT NULL ORDER BY y DESC"""
        )
        years = [r["y"] for r in cur.fetchall()]
        cur.execute(
            """SELECT count(*) AS total,
                      min(published_date) AS desde, max(published_date) AS hasta
               FROM articles"""
        )
        stats = cur.fetchone()

    return templates.TemplateResponse(request, "index.html", {
        "articles": articles, "total": total, "page": page,
        "pages": max(1, -(-total // PER_PAGE)),
        "q": q, "pub": pub, "year": year,
        "pubs": pubs, "years": years, "stats": stats,
    })


@app.get("/articulo/{article_id}", response_class=HTMLResponse)
def article(request: Request, article_id: int, _: None = Depends(require_auth)):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.*, p.name AS pub_name, p.slug AS pub_slug,
                      i.number AS issue_number, i.title AS issue_title,
                      i.url AS issue_url, i.pdf_url
               FROM articles a
               JOIN publications p ON p.id = a.publication_id
               LEFT JOIN issues i ON i.id = a.issue_id
               WHERE a.id = %s""",
            (article_id,),
        )
        art = cur.fetchone()
    if not art:
        raise HTTPException(404, "Artículo no encontrado")
    paragraphs = [p.strip() for p in (art["body"] or "").split("\n\n") if p.strip()]
    return templates.TemplateResponse(request, "article.html", {
        "a": art, "paragraphs": paragraphs,
    })


@app.get("/salud")
def health():
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
