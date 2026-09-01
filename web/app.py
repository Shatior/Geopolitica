"""Frontend de consulta de la hemeroteca — sistema visual «Prusia y bronce».

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
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from markupsafe import escape

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD")
PER_PAGE = 25

PRUSIA, BRONCE, GRIS = "#1f3f66", "#a9772c", "#5d6672"
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

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


def fecha_es(d: date) -> str:
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def fecha_corta(d: date | None) -> str:
    return f"{d.day} {MESES[d.month - 1][:3]} {d.year}" if d else ""


def common_ctx(cur) -> dict:
    cur.execute(
        """SELECT count(*) AS total, count(DISTINCT issue_id) AS issues,
                  min(published_date) AS desde, max(published_date) AS hasta
           FROM articles"""
    )
    stats = cur.fetchone()
    cur.execute("SELECT slug, name FROM publications ORDER BY slug")
    pubs = cur.fetchall()
    cur.execute(
        """SELECT DISTINCT EXTRACT(YEAR FROM published_date)::int AS y
           FROM articles WHERE published_date IS NOT NULL ORDER BY y DESC"""
    )
    years = [r["y"] for r in cur.fetchall()]
    return {
        "hoy": fecha_es(date.today()), "stats": stats,
        "pubs": pubs, "years": years, "fecha_corta": fecha_corta,
    }


# ------------------------------------------------------------------ gráficos

def sparkline(por_anyo: list[tuple[int, int]], w: int = 300, h: int = 56,
              color: str = BRONCE) -> str:
    """SVG de línea simple para una serie (año, nº de menciones)."""
    if len(por_anyo) < 2:
        return ""
    vals = [c for _, c in por_anyo]
    vmax = max(max(vals), 1)
    n = len(por_anyo)
    pts = " ".join(
        f"{i * w / (n - 1):.0f},{h - 6 - (c / vmax) * (h - 12):.0f}"
        for i, (_, c) in enumerate(por_anyo)
    )
    lx, ly = pts.rsplit(" ", 1)[-1].split(",")
    return (
        f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"></polyline>'
        f'<circle cx="{lx}" cy="{ly}" r="4" fill="{color}"></circle></svg>'
    )


def linechart(series: list[dict], y0: int, y1: int) -> str:
    """Gráfico de líneas del panel de tendencias. Cada serie:
    {"label", "color", "data": {año: menciones}}."""
    W, H, X0, X1, YT, YB = 1120, 252, 46, 1024, 12, 220
    vmax = max([1] + [c for s in series for c in s["data"].values()])
    step = max(1, -(-vmax // 4))  # 4 tramos "bonitos"
    for nice in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if nice >= step:
            step = nice
            break
    top = step * 4
    years = list(range(y0, y1 + 1))
    nx = max(1, len(years) - 1)

    def x(i): return X0 + i * (X1 - X0) / nx
    def y(v): return YB - v * (YB - YT) / top

    out = [f'<svg width="100%" height="{H}" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">']
    for k in range(5):
        v = k * step
        out.append(
            f'<line x1="{X0}" y1="{y(v):.0f}" x2="{X1}" y2="{y(v):.0f}" stroke="#d3d8da" stroke-width="1"></line>'
            f'<text x="{X0 - 8}" y="{y(v) + 4:.0f}" text-anchor="end" font-family="system-ui" font-size="11" fill="#79828d">{v}</text>'
        )
    for i, yr in enumerate(years):
        if yr % 3 == 0 or yr == y1:
            out.append(
                f'<text x="{x(i):.0f}" y="238" text-anchor="middle" font-family="system-ui" font-size="11" fill="#79828d">{yr}</text>'
            )
    label_ys: list[float] = []
    for s in series:
        pts = " ".join(f"{x(i):.0f},{y(s['data'].get(yr, 0)):.0f}" for i, yr in enumerate(years))
        out.append(
            f'<polyline points="{pts}" fill="none" stroke="{s["color"]}" stroke-width="2.5" stroke-linejoin="round"></polyline>'
        )
        ly = y(s["data"].get(y1, 0))
        while any(abs(ly - o) < 14 for o in label_ys):  # separa etiquetas superpuestas
            ly -= 14
        label_ys.append(ly)
        out.append(
            f'<circle cx="{X1:.0f}" cy="{y(s["data"].get(y1, 0)):.0f}" r="4" fill="{s["color"]}"></circle>'
            f'<text x="{X1 + 10:.0f}" y="{ly + 4:.0f}" font-family="system-ui" font-size="12" '
            f'font-weight="600" fill="{s["color"]}">{escape(s["label"])}</text>'
        )
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------- rutas

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
    portada = not q and not pub and not year and page == 1

    with db() as conn, conn.cursor() as cur:
        ctx = common_ctx(cur)

        if portada:
            cur.execute(
                """SELECT i.id, i.number, i.title, i.published_date, i.pdf_url,
                          p.name AS pub_name, p.slug AS pub_slug
                   FROM issues i JOIN publications p ON p.id = i.publication_id
                   WHERE EXISTS (SELECT 1 FROM articles a WHERE a.issue_id = i.id)
                   ORDER BY i.published_date DESC NULLS LAST LIMIT 1"""
            )
            numero = cur.fetchone()
            destacado, indice, previos, tendencia = None, [], [], None
            if numero:
                cur.execute(
                    """SELECT id, title, published_date, tags,
                              coalesce(subtitle, left(body, 420)) AS entradilla,
                              length(coalesce(body, '')) AS len
                       FROM articles WHERE issue_id = %s ORDER BY len DESC""",
                    (numero["id"],),
                )
                arts = cur.fetchall()
                if arts:
                    destacado = arts[0]
                    indice = sorted(arts[1:], key=lambda a: a["id"])
                cur.execute(
                    """SELECT DISTINCT ON (i.id) i.id AS issue_id, i.number,
                              i.published_date, a.id, a.title,
                              coalesce(a.subtitle, left(a.body, 200)) AS entradilla
                       FROM issues i
                       JOIN articles a ON a.issue_id = i.id
                       WHERE i.published_date < %s AND i.publication_id = (
                             SELECT id FROM publications WHERE slug = %s)
                       ORDER BY i.id, length(a.body) DESC""",
                    (numero["published_date"], numero["pub_slug"]),
                )
                todos_previos = sorted(
                    cur.fetchall(), key=lambda r: r["published_date"], reverse=True
                )
                previos = todos_previos[:2]

                tema = next((t for a in ([destacado] + indice) if a for t in a["tags"]), None)
                if tema:
                    cur.execute(
                        """SELECT EXTRACT(YEAR FROM published_date)::int AS y, count(*) AS n
                           FROM articles
                           WHERE tsv @@ websearch_to_tsquery('spanish', %s)
                             AND published_date IS NOT NULL
                           GROUP BY y ORDER BY y""",
                        (tema,),
                    )
                    serie = [(r["y"], r["n"]) for r in cur.fetchall()]
                    if len(serie) >= 2:
                        tendencia = {
                            "tema": tema,
                            "svg": sparkline(serie),
                            "desde": serie[0][0], "hasta": serie[-1][0],
                            "total": sum(n for _, n in serie),
                        }
            return templates.TemplateResponse(request, "portada.html", {
                **ctx, "seccion": "archivo", "q": "", "pub": "", "year": 0,
                "numero": numero, "destacado": destacado, "indice": indice,
                "previos": previos, "tendencia": tendencia,
            })

        # --- modo resultados ---
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
            "ts_rank(a.tsv, websearch_to_tsquery('spanish', %(q)s)) DESC," if q else ""
        )
        cur.execute(
            f"""SELECT count(*) AS n FROM articles a
                JOIN publications p ON p.id = a.publication_id WHERE {where_sql}""",
            params,
        )
        total = cur.fetchone()["n"]
        cur.execute(
            f"""SELECT a.id, a.title, a.published_date, a.tags, a.is_full,
                       i.number AS issue_number,
                       p.slug AS pub_slug, p.name AS pub_name,
                       {snippet_sql} AS snippet
                FROM articles a
                JOIN publications p ON p.id = a.publication_id
                LEFT JOIN issues i ON i.id = a.issue_id
                WHERE {where_sql}
                ORDER BY {order_sql} a.published_date DESC NULLS LAST, a.id DESC
                LIMIT %(limit)s OFFSET %(offset)s""",
            {**params, "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE},
        )
        articles = cur.fetchall()

    return templates.TemplateResponse(request, "resultados.html", {
        **ctx, "seccion": "archivo",
        "articles": articles, "total": total, "page": page,
        "pages": max(1, -(-total // PER_PAGE)),
        "q": q, "pub": pub, "year": year,
    })


@app.get("/articulo/{article_id}", response_class=HTMLResponse)
def article(request: Request, article_id: int, _: None = Depends(require_auth)):
    with db() as conn, conn.cursor() as cur:
        ctx = common_ctx(cur)
        cur.execute(
            """SELECT a.*, p.name AS pub_name, p.slug AS pub_slug,
                      i.number AS issue_number, i.url AS issue_url, i.pdf_url
               FROM articles a
               JOIN publications p ON p.id = a.publication_id
               LEFT JOIN issues i ON i.id = a.issue_id
               WHERE a.id = %s""",
            (article_id,),
        )
        art = cur.fetchone()
        if not art:
            raise HTTPException(404, "Artículo no encontrado")
        hermanos = []
        if art["issue_id"]:
            cur.execute(
                """SELECT id, title FROM articles
                   WHERE issue_id = %s AND id <> %s ORDER BY id""",
                (art["issue_id"], article_id),
            )
            hermanos = cur.fetchall()
        cur.execute(
            """SELECT id, title FROM articles
               WHERE publication_id = %s AND (published_date, id) < (%s, %s)
               ORDER BY published_date DESC, id DESC LIMIT 1""",
            (art["publication_id"], art["published_date"], article_id),
        )
        anterior = cur.fetchone()
        cur.execute(
            """SELECT id, title FROM articles
               WHERE publication_id = %s AND (published_date, id) > (%s, %s)
               ORDER BY published_date ASC, id ASC LIMIT 1""",
            (art["publication_id"], art["published_date"], article_id),
        )
        siguiente = cur.fetchone()

    paragraphs = [p.strip() for p in (art["body"] or "").split("\n\n") if p.strip()]
    return templates.TemplateResponse(request, "article.html", {
        **ctx, "seccion": "archivo", "q": "", "pub": "", "year": 0,
        "a": art, "paragraphs": paragraphs, "hermanos": hermanos,
        "anterior": anterior, "siguiente": siguiente,
    })


@app.get("/tendencias", response_class=HTMLResponse)
def tendencias(
    request: Request,
    t1: str = "",
    t2: str = "",
    _: None = Depends(require_auth),
):
    t1, t2 = t1.strip(), t2.strip()
    terms = [t for t in (t1, t2) if t]
    colors = [PRUSIA, BRONCE]

    with db() as conn, conn.cursor() as cur:
        ctx = common_ctx(cur)
        y0 = ctx["stats"]["desde"].year if ctx["stats"]["desde"] else 2009
        y1 = ctx["stats"]["hasta"].year if ctx["stats"]["hasta"] else date.today().year

        series, totales, relevantes = [], [], []
        for i, term in enumerate(terms):
            cur.execute(
                """SELECT EXTRACT(YEAR FROM published_date)::int AS y, count(*) AS n
                   FROM articles
                   WHERE tsv @@ websearch_to_tsquery('spanish', %s)
                     AND published_date IS NOT NULL
                   GROUP BY y""",
                (term,),
            )
            data = {r["y"]: r["n"] for r in cur.fetchall()}
            series.append({"label": term, "color": colors[i], "data": data})
            totales.append({"term": term, "color": colors[i], "n": sum(data.values())})
        chart = linechart(series, y0, y1) if series else None

        if terms:
            cur.execute(
                """SELECT a.id, a.title, a.published_date, p.name AS pub_name
                   FROM articles a JOIN publications p ON p.id = a.publication_id
                   WHERE a.tsv @@ websearch_to_tsquery('spanish', %(t)s)
                   ORDER BY ts_rank(a.tsv, websearch_to_tsquery('spanish', %(t)s)) DESC
                   LIMIT 6""",
                {"t": terms[0]},
            )
            relevantes = cur.fetchall()

        cur.execute(
            """SELECT tag, count(*) AS n FROM (
                   SELECT unnest(tags) AS tag FROM articles
                   WHERE EXTRACT(YEAR FROM published_date) = %s
               ) t GROUP BY tag ORDER BY n DESC LIMIT 7""",
            (y1,),
        )
        temas = cur.fetchall()
        max_tema = temas[0]["n"] if temas else 1

    return templates.TemplateResponse(request, "tendencias.html", {
        **ctx, "seccion": "tendencias", "q": "", "pub": "", "year": 0,
        "t1": t1, "t2": t2, "terms": terms, "chart": chart,
        "totales": totales, "relevantes": relevantes,
        "temas": temas, "max_tema": max_tema, "anyo_actual": y1,
    })


@app.get("/salud")
def health():
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
