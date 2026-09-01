"""Radiografía del corpus: qué materia prima hay realmente para analizar.

    python -m db.inventario

Solo consulta la base de datos (no toca politicaexterior.com). Sirve para
diseñar con datos en la mano: cobertura por año, metadatos disponibles,
longitudes, vocabulario y huecos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import aviso_database_url, load_config  # noqa: E402


def barra(n: int, maximo: int, ancho: int = 28) -> str:
    return "█" * max(0, round(n * ancho / max(maximo, 1)))


def main() -> int:
    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
        SOLO = "kind <> 'portada'"

        print("=" * 66)
        print("INVENTARIO DEL CORPUS")
        print("=" * 66)

        cur.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE is_full),
                   sum(length(coalesce(body,''))), avg(length(coalesce(body,'')))::int
            FROM articles WHERE {SOLO}""")
        n, completos, chars, media = cur.fetchone()
        print(f"\nAnálisis: {n}  ·  con texto completo: {completos} ({completos*100//max(n,1)}%)")
        print(f"Texto total: {chars/1_000_000:.1f}M caracteres  ·  media {media} por análisis")
        print(f"Estimación para IA: ~{chars/3.7/1_000_000:.1f}M tokens de entrada")

        print("\n--- METADATOS DISPONIBLES ---")
        cur.execute(f"""
            SELECT count(*) FILTER (WHERE cardinality(tags) > 0),
                   count(*) FILTER (WHERE cardinality(authors) > 0),
                   count(*) FILTER (WHERE subtitle IS NOT NULL AND subtitle <> ''),
                   count(*) FILTER (WHERE published_date IS NOT NULL),
                   count(*) FILTER (WHERE issue_id IS NOT NULL)
            FROM articles WHERE {SOLO}""")
        etiq, aut, sub, fecha, num = cur.fetchone()
        for nombre, v in [("con etiquetas", etiq), ("con autor", aut),
                          ("con subtítulo", sub), ("con fecha", fecha),
                          ("ligados a un número", num)]:
            print(f"  {nombre:24} {v:5}  ({v*100//max(n,1):3}%)  {barra(v, n)}")

        print("\n--- COBERTURA POR AÑO (análisis por año) ---")
        cur.execute(f"""
            SELECT EXTRACT(YEAR FROM published_date)::int AS y,
                   count(*) FILTER (WHERE p.slug = 'informe-semanal'),
                   count(*) FILTER (WHERE p.slug = 'politica-exterior')
            FROM articles a JOIN publications p ON p.id = a.publication_id
            WHERE {SOLO.replace('kind', 'a.kind')} AND published_date IS NOT NULL
            GROUP BY y ORDER BY y""")
        filas = cur.fetchall()
        mx = max((s + r for _, s, r in filas), default=1)
        for y, semanal, revista in filas:
            print(f"  {y}  IS:{semanal:4}  PE:{revista:4}  {barra(semanal + revista, mx)}")

        print("\n--- ETIQUETAS MÁS FRECUENTES ---")
        cur.execute(f"""
            SELECT tag, count(*) FROM (
                SELECT unnest(tags) AS tag FROM articles WHERE {SOLO}
            ) t GROUP BY tag ORDER BY count(*) DESC LIMIT 15""")
        top = cur.fetchall()
        for tag, c in top:
            print(f"  {c:5}  {tag[:50]}")
        cur.execute(f"SELECT count(DISTINCT tag) FROM (SELECT unnest(tags) AS tag FROM articles WHERE {SOLO}) t")
        print(f"  → {cur.fetchone()[0]} etiquetas distintas en total")

        # Cuántas piezas trae cada número. El sitio publica el Informe Semanal
        # partido en secciones, y si esa cifra cambia con los años no estamos
        # comparando lo mismo: un año con una sola pieza por número puede
        # significar que del informe solo se capturó una parte.
        print("\n--- PIEZAS POR NÚMERO (¿está el informe entero?) ---")
        cur.execute(f"""
            SELECT p.slug,
                   EXTRACT(YEAR FROM i.published_date)::int AS anyo,
                   count(DISTINCT i.id) AS numeros,
                   count(*) AS piezas,
                   round(count(*)::numeric / count(DISTINCT i.id), 1) AS por_numero
              FROM articles a
              JOIN issues i ON i.id = a.issue_id
              JOIN publications p ON p.id = i.publication_id
             WHERE a.{SOLO} AND i.published_date IS NOT NULL
             GROUP BY 1, 2 ORDER BY 1, 2
        """)
        actual = None
        for slug, anyo, numeros, piezas, por in cur.fetchall():
            if slug != actual:
                print(f"  {slug}")
                actual = slug
            aviso = "   <-- una sola pieza" if por < 1.5 else ""
            print(f"    {anyo}  {numeros:4} nums {piezas:5} piezas "
                  f"{por:5} por num{aviso}")

        # La forma de la URL distingue las dos épocas del sitio y delata si un
        # hueco es suyo o nuestro: si los años antiguos usan el mismo patrón
        # que los modernos pero traen una pieza por número, es que se nos
        # escaparon cuatro de cada cinco.
        print("\n--- FORMA DE LAS URL POR AÑO (informe semanal) ---")
        cur.execute(f"""
            SELECT EXTRACT(YEAR FROM a.published_date)::int AS anyo,
                   count(*) FILTER (WHERE a.url LIKE '%/articulo-completo/%') AS completo,
                   count(*) FILTER (WHERE a.url LIKE '%/articulo/%') AS articulo,
                   count(*) AS total,
                   count(*) FILTER (WHERE i.published_date IS NULL) AS sin_fecha
              FROM articles a
              JOIN publications p ON p.id = a.publication_id
              LEFT JOIN issues i ON i.id = a.issue_id
             WHERE p.slug = 'informe-semanal' AND a.{SOLO}
               AND a.published_date IS NOT NULL
             GROUP BY 1 ORDER BY 1
        """)
        print(f"    {'año':5} {'/articulo-completo/':>19} {'/articulo/':>11} "
              f"{'total':>6} {'nº sin fecha':>13}")
        for anyo, completo, articulo, total, sin_fecha in cur.fetchall():
            print(f"    {anyo:5} {completo:19} {articulo:11} {total:6} {sin_fecha:13}")

        print("\n--- ¿QUÉ ES LA PIEZA /articulo/? (informe semanal) ---")
        print("    Cada número de 2021 en adelante trae 4 piezas /articulo-completo/")
        print("    y 1 pieza /articulo/. De 2009-2020 solo tenemos la /articulo/.")
        print("    Si las dos formas miden lo mismo, la vieja es una sección y")
        print("    faltan cuatro; si la /articulo/ es mucho más corta, es un")
        print("    sumario y de esos doce años no tenemos análisis.")
        cur.execute(f"""
            SELECT CASE WHEN a.url LIKE '%/articulo-completo/%'
                        THEN 'completo' ELSE 'articulo' END AS forma,
                   CASE WHEN EXTRACT(YEAR FROM a.published_date) >= 2021
                        THEN 'desde 2021' ELSE 'hasta 2020' END AS epoca,
                   count(*),
                   percentile_disc(0.50) WITHIN GROUP (
                       ORDER BY length(coalesce(a.body,''))),
                   percentile_disc(0.10) WITHIN GROUP (
                       ORDER BY length(coalesce(a.body,''))),
                   percentile_disc(0.90) WITHIN GROUP (
                       ORDER BY length(coalesce(a.body,''))),
                   count(*) FILTER (WHERE a.is_full)
              FROM articles a
              JOIN publications p ON p.id = a.publication_id
             WHERE p.slug = 'informe-semanal' AND a.{SOLO}
               AND a.published_date IS NOT NULL
             GROUP BY 1, 2 ORDER BY 2 DESC, 1
        """)
        print(f"    {'forma':10} {'época':11} {'piezas':>7} {'mediana':>8} "
              f"{'p10':>7} {'p90':>7} {'completas':>10}")
        for forma, epoca, n, p50, p10, p90, full in cur.fetchall():
            print(f"    {forma:10} {epoca:11} {n:7} {p50:8} {p10:7} {p90:7} "
                  f"{full:10}")

        print("\n--- TITULARES DE UN NÚMERO DE CADA ÉPOCA ---")
        cur.execute("""
            SELECT i.id FROM issues i
              JOIN publications p ON p.id = i.publication_id
             WHERE p.slug = 'informe-semanal' AND i.published_date IS NOT NULL
             ORDER BY abs(EXTRACT(YEAR FROM i.published_date) - 2023), i.id
             LIMIT 1
        """)
        recientes = [r[0] for r in cur.fetchall()]
        cur.execute("""
            SELECT a.issue_id FROM articles a
              JOIN publications p ON p.id = a.publication_id
             WHERE p.slug = 'informe-semanal' AND a.issue_id IS NOT NULL
               AND EXTRACT(YEAR FROM a.published_date) BETWEEN 2013 AND 2016
             ORDER BY a.published_date LIMIT 3
        """)
        antiguos = [r[0] for r in cur.fetchall()]
        for issue_id in recientes + antiguos:
            cur.execute("""
                SELECT i.title, i.published_date FROM issues i WHERE i.id = %s
            """, (issue_id,))
            fila = cur.fetchone()
            print(f"\n    Número: {fila[0]}  ({fila[1]})")
            cur.execute("""
                SELECT a.title, length(coalesce(a.body,'')), a.url
                  FROM articles a WHERE a.issue_id = %s ORDER BY a.id
            """, (issue_id,))
            for titulo, largo, url in cur.fetchall():
                forma = "completo" if "/articulo-completo/" in url else "articulo"
                print(f"      [{forma:8}] {largo:6} chars  {(titulo or '')[:60]}")

        print("\n--- LONGITUD DE LOS ANÁLISIS ---")
        cur.execute(f"""
            SELECT p.slug,
                   percentile_disc(0.25) WITHIN GROUP (ORDER BY length(coalesce(body,''))),
                   percentile_disc(0.50) WITHIN GROUP (ORDER BY length(coalesce(body,''))),
                   percentile_disc(0.90) WITHIN GROUP (ORDER BY length(coalesce(body,'')))
            FROM articles a JOIN publications p ON p.id = a.publication_id
            WHERE {SOLO.replace('kind', 'a.kind')} GROUP BY p.slug ORDER BY p.slug""")
        for slug, p25, p50, p90 in cur.fetchall():
            print(f"  {slug:20} mediana {p50:6} chars   (p25 {p25} · p90 {p90})")

        print("\n--- VOCABULARIO (base para señales débiles) ---")
        cur.execute(f"""
            SELECT count(*) FROM (
                SELECT DISTINCT word FROM ts_stat(
                    $$SELECT tsv FROM articles WHERE {SOLO}$$
                )
            ) v""")
        print(f"  {cur.fetchone()[0]} términos distintos indexados")

        print("\n--- MATERIAL PARA EL RESCATE Y LAS FUENTES ---")
        cur.execute("SELECT count(*), count(*) FILTER (WHERE pdf_url IS NOT NULL) FROM issues")
        ni, npdf = cur.fetchone()
        print(f"  Números: {ni}  ·  con PDF disponible: {npdf} ({npdf*100//max(ni,1)}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
