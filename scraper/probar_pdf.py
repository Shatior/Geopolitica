"""¿Da acceso el sitio a los PDF de los números, con nuestra suscripción?

    python -m scraper.probar_pdf --publicacion politica-exterior --limite 2

Cuando al pulsar «PDF del número» la web responde «no tiene acceso a este
documento», hay tres explicaciones posibles y conviene distinguirlas antes de
tocar nada:

1. El navegador desde el que se pulsa no tiene sesión iniciada en
   politicaexterior.com (el enlace apunta a su web, no a la nuestra).
2. Las cookies que guardamos han caducado.
3. La suscripción no cubre la descarga de ese PDF.

Esta comprobación resuelve las dos últimas: pide el PDF con nuestras cookies y
cuenta qué contesta el servidor. Lee el recurso en streaming y corta tras los
primeros bytes —los justos para distinguir un PDF de una página de aviso—, de
modo que no se descarga un número entero de la revista para averiguarlo. No
escribe nada en la base de datos.
"""
from __future__ import annotations

import argparse
import logging
import sys

import psycopg
from psycopg.rows import dict_row

from .config import aviso_database_url, load_config
from .session import PoliteSession

log = logging.getLogger("probar_pdf")

AVISO = "no tiene acceso"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Comprobar acceso a los PDF")
    ap.add_argument("--publicacion", default=None, help="slug; por defecto, ambas")
    ap.add_argument("--limite", type=int, default=3)
    ap.add_argument("--comparar", action="store_true",
                    help="pedir además el mismo documento SIN sesión, para "
                         "averiguar qué significa el mensaje de aviso")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    filtros = ["i.pdf_url IS NOT NULL"]
    params: dict = {}
    if args.publicacion:
        filtros.append("p.slug = %(pub)s")
        params["pub"] = args.publicacion

    with psycopg.connect(cfg.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT p.slug, i.number, i.published_date, i.pdf_url
                FROM issues i JOIN publications p ON p.id = i.publication_id
                WHERE {' AND '.join(filtros)}
                ORDER BY i.published_date DESC
                LIMIT {int(args.limite)}""",
            params,
        )
        numeros = cur.fetchall()

    if not numeros:
        print("No hay números con enlace a PDF que comprobar.")
        return 0

    sess = PoliteSession(cfg.base_url, cfg.throttle)
    if cfg.cookies_file:
        sess.load_cookies(cfg.cookies_file)
    autenticada = sess.is_logged_in()
    print(f"Sesión autenticada en politicaexterior.com: {autenticada}")
    if not autenticada:
        print("→ Las cookies guardadas ya no valen. Hay que exportarlas de nuevo\n"
              "  desde un navegador con la sesión abierta y actualizar PE_COOKIES.")
    print()

    # Sin sesión: si la respuesta anónima es exactamente el aviso que ve el
    # navegador, entonces ese mensaje significa «no has iniciado sesión» y no
    # «tu suscripción no cubre esto». Es la única forma de distinguirlos.
    if args.comparar:
        import requests
        anon = requests.Session()
        anon.headers.update(sess.session.headers)
        print("SIN SESIÓN (como un visitante cualquiera):")
        for n in numeros[:2]:
            r = anon.get(n["pdf_url"], timeout=45, stream=True)
            try:
                inicio = next(r.iter_content(2048), b"")
            finally:
                r.close()
            tipo = r.headers.get("Content-Type", "")
            texto = inicio.decode("utf-8", "ignore").lower()
            que = ("un PDF" if inicio[:5] == b"%PDF-" or "pdf" in tipo.lower()
                   else f"el aviso «{AVISO}»" if AVISO in texto
                   else f"otra cosa ({r.status_code}, {tipo[:30]})")
            print(f"  nº {n['number']}: {r.status_code} → {que}")
        print()

    ok = 0
    for n in numeros:
        estado, cabeceras, inicio = sess.asomarse(n["pdf_url"])
        tipo = cabeceras.get("Content-Type", "")
        es_pdf = estado == 200 and ("pdf" in tipo.lower() or inicio[:5] == b"%PDF-")
        texto = inicio.decode("utf-8", "ignore").lower()
        denegado = estado in (401, 403) or AVISO in texto

        veredicto = ("PDF accesible" if es_pdf else
                     f"ACCESO DENEGADO ({estado})" if denegado else
                     f"respuesta inesperada ({estado}, {tipo[:30]})")
        if es_pdf:
            ok += 1
        print(f"  {n['slug']:20} nº {str(n['number'] or '?'):>6}  "
              f"{n['published_date']}  →  {veredicto}")
        print(f"      {n['pdf_url']}")

    print(f"\n{ok} de {len(numeros)} PDF accesibles con nuestra suscripción.")
    if ok == 0 and autenticada:
        print("La sesión es válida pero el sitio niega el documento: el problema\n"
              "no es nuestro, es el alcance de la suscripción para esos PDF.")
    elif ok and autenticada:
        print("Nuestra sesión sí puede descargarlos, así que el fallo al pulsar el\n"
              "enlace es del navegador: hay que iniciar sesión en\n"
              "politicaexterior.com en ese mismo navegador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
