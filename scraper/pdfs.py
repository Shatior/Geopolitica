"""Rescata el texto de los artículos incompletos desde el PDF de su número.

Los números antiguos del Informe Semanal (2009-2012) y algunos de la revista
publican en la web solo una ficha con un extracto: el texto íntegro está en el
PDF del número, cuyo enlace guardamos al scrapear. Este módulo descarga esos
PDFs (con el mismo ritmo educado del scraper), extrae su texto y lo reparte
entre los artículos del número.

    python -m scraper.pdfs                 # todos los números con huecos
    python -m scraper.pdfs --limit 5       # prueba corta
    python -m scraper.pdfs --publication informe-semanal

Escribe data/parsed/articles_pdf.jsonl, que `db.load` aplica ENCIMA de
articles.jsonl (gana el texto rescatado). Es reanudable: data/pdf_state.json
recuerda los números ya procesados.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata

from .config import (DATA_DIR, PARSED_DIR, ROOT, aviso_database_url,
                      ensure_dirs, load_config)
from .parse import MIN_FULL_BODY
from .session import PoliteSession, ScraperBlocked

log = logging.getLogger("pdfs")

PDF_DIR = DATA_DIR / "pdfs"
PDF_STATE = DATA_DIR / "pdf_state.json"
OUT = PARSED_DIR / "articles_pdf.jsonl"
SEP = "\x00PARR\x00"  # centinela interno para marcar separación de párrafos


# --------------------------------------------------------------- utilidades
def normalizar(s: str) -> tuple[str, list[int]]:
    """Versión comparable de un texto: sin acentos, en minúsculas y SOLO
    alfanuméricos, descartando espacios y puntuación. Se descartan porque la
    extracción de PDF pierde o inventa espacios con frecuencia ("El bume rán"),
    lo que impediría localizar los titulares. Devuelve además el índice
    original de cada carácter, para recortar después sobre el texto real."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        desc = unicodedata.normalize("NFKD", ch)
        desc = "".join(c for c in desc if not unicodedata.combining(c)).lower()
        for c in desc:
            if c.isalnum():
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def limpiar_pdf(texto: str) -> str:
    """Une palabras partidas por guion, junta las líneas de un mismo párrafo y
    conserva los saltos dobles del PDF como separación de párrafos."""
    texto = texto.replace("\r", "")
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)      # guion de corte de línea
    texto = re.sub(r"\n{2,}", SEP, texto)               # párrafo → centinela
    texto = re.sub(r"[ \t]*\n[ \t]*", " ", texto)       # salto simple → espacio
    texto = texto.replace(SEP, "\n\n")
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    return texto.strip()


def extraer_texto(ruta) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(ruta))
    paginas = []
    for pag in reader.pages:
        try:
            paginas.append(pag.extract_text() or "")
        except Exception as exc:  # una página corrupta no debe tirar el número
            log.warning("  página ilegible en %s: %s", ruta.name, exc)
    return limpiar_pdf("\n\n".join(paginas))


def repartir(texto: str, articulos: list[dict]) -> dict[str, str]:
    """Asigna a cada artículo el tramo del PDF que va desde su titular hasta el
    titular siguiente. Con un solo artículo, le corresponde todo el texto."""
    if not articulos:
        return {}
    if len(articulos) == 1:
        return {articulos[0]["url"]: texto}

    norm, idx = normalizar(texto)
    posiciones = []
    sin_localizar = []
    for art in articulos:
        titulo = (art.get("title") or "").strip()
        ntit, _ = normalizar(titulo)
        if len(ntit) < 10:          # titulares muy cortos darían falsos positivos
            continue
        p = -1
        for corte in (len(ntit), 40, 30, 22):
            if corte > len(ntit):
                continue
            p = norm.find(ntit[:corte])
            if p != -1:
                break
        if p != -1:
            posiciones.append((idx[p], art))
        else:
            sin_localizar.append(titulo)
    if sin_localizar:
        log.info("  sin localizar en el PDF: %s",
                 "; ".join(t[:45] for t in sin_localizar[:4]))

    if not posiciones:
        return {}
    posiciones.sort(key=lambda t: t[0])
    tramos: dict[str, str] = {}
    for i, (ini, art) in enumerate(posiciones):
        fin = posiciones[i + 1][0] if i + 1 < len(posiciones) else len(texto)
        tramos[art["url"]] = texto[ini:fin].strip()
    return tramos


def leer_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]



# ------------------------------------------------------- origen: base de datos
def asegurar_esquema(database_url: str) -> None:
    """Aplica db/schema.sql (idempotente) para que este módulo pueda ejecutarse
    por sí solo, sin depender de que db.load haya corrido antes."""
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        conn.commit()


def pendientes_desde_db(database_url: str, pubs: set[str]) -> list[tuple[dict, list[dict]]]:
    """Números con al menos un artículo incompleto y PDF disponible, leídos de
    la base de datos. Es naturalmente reanudable: al marcar los artículos como
    completos dejan de aparecer aquí."""
    import psycopg
    from psycopg.rows import dict_row

    out: list[tuple[dict, list[dict]]] = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id, i.url, i.number, i.pdf_url, p.slug AS publication
               FROM issues i JOIN publications p ON p.id = i.publication_id
               WHERE i.pdf_url IS NOT NULL AND p.slug = ANY(%s)
                 AND EXISTS (SELECT 1 FROM articles a
                             WHERE a.issue_id = i.id AND NOT a.is_full
                               AND a.kind <> 'portada'
                               AND a.pdf_rescued_at IS NULL)
               ORDER BY i.published_date DESC NULLS LAST""",
            (list(pubs),),
        )
        numeros = cur.fetchall()
        for iss in numeros:
            cur.execute(
                """SELECT url, title, coalesce(body, '') AS body, is_full
                   FROM articles WHERE issue_id = %s AND kind <> 'portada'
                   ORDER BY id""",
                (iss["id"],),
            )
            out.append((iss, cur.fetchall()))
    return out


def guardar_en_db(database_url: str, registros: list[dict],
                  intentadas: list[str] | None = None) -> None:
    """Escribe el texto rescatado en la base de datos y deja constancia del
    intento en todos los artículos del número, incluidos aquellos para los que
    el PDF no aportó texto: así no se vuelven a descargar en cada ejecución."""
    import psycopg

    if not registros and not intentadas:
        return
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        for rec in registros:
            cur.execute(
                """UPDATE articles
                   SET body = %s, is_full = %s, pdf_rescued_at = now()
                   WHERE url = %s""",
                (rec["body"], rec["is_full"], rec["url"]),
            )
        if intentadas:
            cur.execute(
                """UPDATE articles SET pdf_rescued_at = now()
                   WHERE url = ANY(%s) AND pdf_rescued_at IS NULL""",
                (list(intentadas),),
            )
        conn.commit()


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rescata texto desde los PDF de los números")
    ap.add_argument("--publication", action="append", help="slug (repetible)")
    ap.add_argument("--limit", type=int, default=0, help="máximo de números en esta ejecución")
    ap.add_argument("--reintentar", action="store_true",
                    help="olvida los intentos previos (pdf_rescued_at) de los "
                         "artículos que siguen incompletos y vuelve a probarlos")
    ap.add_argument("--from-db", action="store_true",
                    help="lee los números pendientes de la base de datos y escribe en ella "
                         "el texto rescatado (para ejecuciones sin ficheros locales, p. ej. CI)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    ensure_dirs()
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    try:
        import pypdf  # noqa: F401
    except ImportError:
        log.error("Falta pypdf. Ejecuta: pip install -r requirements.txt")
        return 2

    pubs = set(args.publication or cfg.publications)
    estado = {}

    if args.from_db:
        aviso = aviso_database_url(cfg.database_url)
        if aviso:
            log.error("%s", aviso)
            return 2
        asegurar_esquema(cfg.database_url)
        if args.reintentar:
            import psycopg
            with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
                cur.execute("UPDATE articles SET pdf_rescued_at = NULL "
                            "WHERE NOT is_full AND pdf_rescued_at IS NOT NULL")
                log.info("Reintentando %d artículos marcados previamente", cur.rowcount)
                conn.commit()
        pendientes = pendientes_desde_db(cfg.database_url, pubs)
    else:
        issues = leer_jsonl(PARSED_DIR / "issues.jsonl")
        articles = list({a["url"]: a for a in leer_jsonl(PARSED_DIR / "articles.jsonl")}.values())
        if not issues or not articles:
            log.error("Faltan data/parsed/issues.jsonl o articles.jsonl "
                      "(¿querías --from-db?)")
            return 2
        por_numero: dict[str, list[dict]] = {}
        for a in articles:
            if a.get("issue_url"):
                por_numero.setdefault(a["issue_url"], []).append(a)
        estado = json.loads(PDF_STATE.read_text(encoding="utf-8")) if PDF_STATE.exists() else {}
        pendientes = []
        for iss in issues:
            if iss.get("publication") not in pubs or not iss.get("pdf_url"):
                continue
            arts = por_numero.get(iss["url"], [])
            if any(not a.get("is_full") for a in arts):
                pendientes.append((iss, arts))
    log.info("Números con artículos incompletos y PDF disponible: %d", len(pendientes))

    sess = PoliteSession(cfg.base_url, cfg.throttle)
    if cfg.cookies_file:
        sess.load_cookies(cfg.cookies_file)
    elif cfg.username and cfg.password:
        sess.login(cfg.username, cfg.password)
    else:
        log.error("Sin autenticación: los PDF son de pago. Configura .env")
        return 2

    hechos = rescatados = 0
    try:
        with open(OUT, "a", encoding="utf-8") as fh:
            for iss, arts in pendientes:
                if estado.get(iss["url"], {}).get("status") == "ok":
                    continue
                if args.limit and hechos >= args.limit:
                    log.info("Alcanzado --limit=%d", args.limit)
                    break

                nombre = f"{iss.get('publication')}-{iss.get('number') or 'sn'}.pdf"
                destino = PDF_DIR / nombre
                if not destino.exists():
                    resp = sess.get(iss["pdf_url"])
                    tipo = resp.headers.get("Content-Type", "")
                    if resp.status_code != 200 or "pdf" not in tipo.lower():
                        log.warning("nº %s: no es un PDF (%s, %s)",
                                    iss.get("number"), resp.status_code, tipo[:40])
                        estado[iss["url"]] = {"status": "no-pdf"}
                        continue
                    destino.write_bytes(resp.content)
                hechos += 1

                texto = extraer_texto(destino)
                if len(texto) < MIN_FULL_BODY:
                    log.warning("nº %s: el PDF apenas tiene texto (%d chars); "
                                "puede estar escaneado", iss.get("number"), len(texto))
                    estado[iss["url"]] = {"status": "sin-texto"}
                    continue

                tramos = repartir(texto, arts)
                n_num = 0
                del_db: list[dict] = []
                intentadas = [a["url"] for a in arts if not a.get("is_full")]
                for art in arts:
                    if art.get("is_full"):
                        continue
                    nuevo = tramos.get(art["url"])
                    if not nuevo or len(nuevo) <= max(len(art.get("body") or ""), 400):
                        continue
                    rec = dict(art)
                    rec["body"] = nuevo
                    rec["is_full"] = len(nuevo) >= MIN_FULL_BODY
                    rec["source"] = "pdf"
                    del_db.append(rec)
                    if not args.from_db:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_num += 1
                if args.from_db:
                    guardar_en_db(cfg.database_url, del_db, intentadas)
                    del_db.clear()
                else:
                    fh.flush()
                rescatados += n_num
                log.info("nº %s: %d artículos rescatados del PDF (%d chars)",
                         iss.get("number"), n_num, len(texto))
                estado[iss["url"]] = {"status": "ok", "rescatados": n_num}
                if not args.from_db:
                    PDF_STATE.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    except ScraperBlocked as exc:
        log.error("%s", exc)
        return 4
    except KeyboardInterrupt:
        log.info("Interrumpido; progreso guardado.")
    finally:
        if not args.from_db:
            PDF_STATE.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")

    log.info("Hecho: %d números procesados, %d artículos rescatados.", hechos, rescatados)
    if not args.from_db:
        log.info("Ahora ejecuta: python -m db.load")
    return 0


if __name__ == "__main__":
    sys.exit(main())
