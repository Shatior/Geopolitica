"""Enriquecer el archivo con Haiku a través de la Batch API.

    python -m enriquecer.run estimar                    # cuánto costaría, sin gastar
    python -m enriquecer.run enviar --limite 40         # prueba piloto
    python -m enriquecer.run estado
    python -m enriquecer.run recoger
    python -m enriquecer.run enviar                     # el archivo entero

Un lote de la Batch API cuesta la mitad y puede tardar hasta 24 horas, así que
enviar y recoger están separados: el identificador del lote queda en la base de
datos y el resultado se puede recoger después, desde otra máquina.

Es reanudable e idempotente: solo se envían análisis con enriched_at nulo, y
recoger dos veces el mismo lote no duplica nada.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import aviso_database_url, load_config  # noqa: E402

from .extraccion import MODELO, interpretar, peticion  # noqa: E402

# Precio de Haiku 4.5, ya con el 50% de descuento del lote ($/millón de tokens).
PRECIO_ENTRADA = 1.00 / 2
PRECIO_SALIDA = 5.00 / 2
CHARS_POR_TOKEN = 3.6      # castellano, medido a ojo sobre este corpus


def _conectar() -> str:
    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        raise SystemExit(aviso)
    return cfg.database_url


def _esquema(conn) -> None:
    """El esquema es idempotente; aplicarlo aquí evita depender de que alguien
    haya lanzado antes la operación de mantenimiento."""
    ruta = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    with conn.cursor() as cur:
        cur.execute(ruta.read_text(encoding="utf-8"))
    conn.commit()


def _pendientes(conn, publicacion: str | None, limite: int) -> list[dict]:
    filtros = ["a.kind <> 'portada'", "a.is_full", "a.enriched_at IS NULL",
               "coalesce(a.body, '') <> ''"]
    params: dict = {}
    if publicacion:
        filtros.append("p.slug = %(pub)s")
        params["pub"] = publicacion
    tope = f"LIMIT {int(limite)}" if limite else ""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""SELECT a.id, a.title, a.body
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE {' AND '.join(filtros)}
                ORDER BY a.published_date DESC, a.id
                {tope}""",
            params,
        )
        return cur.fetchall()


def _coste(articulos: list[dict]) -> tuple[float, float]:
    chars = sum(len(a["body"]) + len(a["title"] or "") for a in articulos)
    entrada = (chars / CHARS_POR_TOKEN + 400 * len(articulos)) / 1e6
    salida = 300 * len(articulos) / 1e6     # respuestas cortas y acotadas
    return entrada * PRECIO_ENTRADA, salida * PRECIO_SALIDA


def guardar(cur, art_id: int, texto_json: str) -> dict | None:
    """Interpreta una respuesta y la escribe. Devuelve None si es inservible.

    Marcar el artículo como enriquecido es parte de la misma transacción que
    insertar sus entidades: si algo falla a medias, el artículo sigue pendiente
    y el siguiente lote volverá a intentarlo en lugar de quedarse a medio
    catalogar para siempre.
    """
    cur.execute("SELECT coalesce(body, '') FROM articles WHERE id = %s", (art_id,))
    fila = cur.fetchone()
    if not fila:
        return None
    datos = interpretar(texto_json, fila[0])
    if datos is None:
        return None

    cur.executemany(
        """INSERT INTO article_entities (article_id, kind, name)
           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
        [(art_id, k, n) for k, n in datos["entidades"]],
    )
    cur.executemany(
        """INSERT INTO article_expectations (article_id, quote)
           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
        [(art_id, c) for c in datos["citas"]],
    )
    cur.execute(
        "UPDATE articles SET enriched_at = now(), enrichment_model = %s WHERE id = %s",
        (MODELO, art_id))
    return datos


# ------------------------------------------------------------------ órdenes
def estimar(conn, args) -> int:
    arts = _pendientes(conn, args.publicacion, args.limite)
    if not arts:
        print("No queda ningún análisis por enriquecer.")
        return 0
    ent, sal = _coste(arts)
    chars = sum(len(a["body"]) for a in arts)
    print(f"Pendientes: {len(arts)} análisis · {chars/1e6:.1f}M caracteres")
    print(f"Modelo: {MODELO} vía Batch API (50% de descuento)")
    print(f"Coste estimado: {ent:.2f} $ de entrada + {sal:.2f} $ de salida "
          f"= {ent + sal:.2f} $")
    print("La estimación es aproximada: cuenta caracteres, no tokens reales.")
    return 0


def enviar(conn, args) -> int:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    arts = _pendientes(conn, args.publicacion, args.limite)
    if not arts:
        print("No queda ningún análisis por enriquecer.")
        return 0
    ent, sal = _coste(arts)
    print(f"Enviando {len(arts)} análisis a {MODELO} "
          f"(coste estimado {ent + sal:.2f} $)…")

    peticiones = [
        Request(custom_id=f"art-{a['id']}",
                params=MessageCreateParamsNonStreaming(
                    **peticion(a["title"] or "", a["body"])))
        for a in arts
    ]
    lote = anthropic.Anthropic().messages.batches.create(requests=peticiones)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO enrichment_batches (id, model, n_requests)
               VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (lote.id, MODELO, len(peticiones)),
        )
    conn.commit()
    print(f"Lote creado: {lote.id}  ({lote.processing_status})")
    print("Anotado en la base de datos. Recoge el resultado más tarde con:\n"
          "  python -m enriquecer.run recoger")
    return 0


def estado(conn, args) -> int:
    import anthropic
    cliente = anthropic.Anthropic()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT id, created_at, n_requests, collected_at, n_ok, n_error
                       FROM enrichment_batches ORDER BY created_at""")
        lotes = cur.fetchall()
        cur.execute("""SELECT count(*) FILTER (WHERE enriched_at IS NOT NULL) AS hechos,
                              count(*) AS total
                       FROM articles
                       WHERE kind <> 'portada' AND is_full""")
        avance = cur.fetchone()

    print(f"Análisis enriquecidos: {avance['hechos']} de {avance['total']} "
          f"({avance['hechos'] * 100 // max(avance['total'], 1)}%)")
    if not lotes:
        print("No hay ningún lote registrado.")
        return 0
    for l in lotes:
        if l["collected_at"]:
            print(f"  {l['id']}  recogido  {l['n_ok']} ok / {l['n_error']} con error")
            continue
        try:
            b = cliente.messages.batches.retrieve(l["id"])
            c = b.request_counts
            print(f"  {l['id']}  {b.processing_status}  "
                  f"({c.succeeded} ok, {c.processing} en curso, {c.errored} error)")
        except Exception as exc:                       # noqa: BLE001
            print(f"  {l['id']}  no se ha podido consultar: {exc}")
    return 0


def recoger(conn, args) -> int:
    import anthropic
    cliente = anthropic.Anthropic()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT id FROM enrichment_batches
                       WHERE collected_at IS NULL ORDER BY created_at""")
        lotes = [r["id"] for r in cur.fetchall()]
    if not lotes:
        print("No hay lotes pendientes de recoger.")
        return 0

    for lote_id in lotes:
        b = cliente.messages.batches.retrieve(lote_id)
        if b.processing_status != "ended":
            print(f"{lote_id}: todavía {b.processing_status}; se recogerá más tarde.")
            continue
        print(f"{lote_id}: terminado, guardando resultados…")
        ok = err = sin_json = citas_ok = citas_malas = 0

        for res in cliente.messages.batches.results(lote_id):
            if not res.custom_id.startswith("art-"):
                continue
            art_id = int(res.custom_id[4:])
            if res.result.type != "succeeded":
                err += 1
                continue

            texto = next((bl.text for bl in res.result.message.content
                          if bl.type == "text"), "")
            with conn.cursor() as cur:
                datos = guardar(cur, art_id, texto)
            if datos is None:
                sin_json += 1
                continue
            ok += 1
            citas_ok += len(datos["citas"])
            citas_malas += datos["citas_descartadas"]

        with conn.cursor() as cur:
            cur.execute("""UPDATE enrichment_batches
                           SET collected_at = now(), n_ok = %s, n_error = %s
                           WHERE id = %s""", (ok, err + sin_json, lote_id))
        conn.commit()
        print(f"  {ok} análisis enriquecidos · {err} con error · "
              f"{sin_json} con respuesta ilegible")
        print(f"  citas prospectivas: {citas_ok} verificadas en el texto, "
              f"{citas_malas} descartadas por no aparecer literalmente")
    return 0


def muestra(conn, args) -> int:
    """Enseña lo extraído de unos cuantos análisis, para juzgar la calidad."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT a.id, a.title, a.published_date
                       FROM articles a
                       WHERE a.enriched_at IS NOT NULL
                       ORDER BY a.enriched_at DESC, a.id LIMIT %s""",
                    (args.limite or 5,))
        arts = cur.fetchall()
        if not arts:
            print("Todavía no hay nada enriquecido.")
            return 0
        for a in arts:
            print(f"\n{'─' * 72}\n{a['published_date']} — {a['title']}")
            cur.execute("""SELECT kind, name FROM article_entities
                           WHERE article_id = %s ORDER BY kind, name""", (a["id"],))
            por_clase: dict[str, list[str]] = {}
            for r in cur.fetchall():
                por_clase.setdefault(r["kind"], []).append(r["name"])
            for clase in ("actor", "lugar", "tema"):
                if por_clase.get(clase):
                    print(f"  {clase + 'es' if clase == 'actor' else clase + 's':8} "
                          f"{', '.join(por_clase[clase])}")
            cur.execute("""SELECT quote FROM article_expectations
                           WHERE article_id = %s LIMIT 2""", (a["id"],))
            for r in cur.fetchall():
                print(f"  futuro   «{r['quote'][:150]}…»")
    return 0


ORDENES = {"estimar": estimar, "enviar": enviar, "estado": estado,
           "recoger": recoger, "muestra": muestra}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Enriquecer el archivo con Haiku")
    ap.add_argument("orden", choices=sorted(ORDENES))
    ap.add_argument("--publicacion", default=None)
    ap.add_argument("--limite", type=int, default=0, help="0 = sin límite")
    args = ap.parse_args(argv)

    with psycopg.connect(_conectar()) as conn:
        _esquema(conn)
        return ORDENES[args.orden](conn, args)


if __name__ == "__main__":
    sys.exit(main())
