"""Cortar del cuerpo de los análisis la cola de «artículos relacionados».

    python -m db.colas                # ensayo en seco: dice qué cortaría
    python -m db.colas --escribir     # corta de verdad
    python -m db.colas --deshacer     # devuelve las colas a su sitio

El cuerpo guardado de 2.748 de los 4.032 análisis termina con el bloque de
navegación del sitio: el rótulo «ARTÍCULOS RELACIONADOS» y los titulares de
otras piezas. En volumen es poco —entre el 1,6% y el 2% del texto—, pero es
precisamente el vocabulario que cuentan las lentes: países, temas y
conflictos. Y no está repartido por igual: lo llevan el 96% de los informes
anteriores a 2021 y solo el 18% de los posteriores. Una diferencia
sistemática entre épocas fabrica tendencias que no existen.

El corte es reversible: lo que se quita se guarda en articles.trimmed_tail,
de modo que --deshacer reconstruye el cuerpo exacto por concatenación. La
columna tsv de búsqueda es GENERATED, así que se actualiza sola.

Dos salvaguardas: no se corta si el rótulo aparece en la primera mitad del
texto (sería parte del análisis, no el pie de página), y se avisa de las
citas verificadas del enriquecimiento que vivían en la cola y dejarían de
poder comprobarse.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import aviso_database_url, load_config  # noqa: E402

# El rótulo que separa el análisis de la navegación. El primero es el que se
# corta; los demás se cuentan para saber si hay más variantes que atender.
ROTULO = "articulos relacionados"
OTROS_ROTULOS = ("te puede interesar", "lee tambien", "leer mas",
                 "mas sobre", "articulos recientes", "sigue leyendo")

# Si el rótulo sale antes de esta fracción del texto no es un pie de página.
POSICION_MINIMA = 0.5

# Aplanar con translate conserva la longitud —un índice sobre la copia vale
# para el original— y recorre los 38 MB del corpus una sola vez.
_ACENTOS = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ",
                         "aaaaaeeeeiiiiooooouuuuncaaaaaeeeeiiiiooooouuuunc")


def _plano(t: str) -> str:
    return t.lower().translate(_ACENTOS)


def corte(llano: str, largo: int) -> int | None:
    """Dónde empieza la cola en un cuerpo ya aplanado, o None si no hay nada
    que cortar. Recibe el aplanado para no rehacerlo en cada llamada."""
    i = llano.find(ROTULO)
    if i < 0 or i < largo * POSICION_MINIMA:
        return None
    salto = llano.rfind("\n", 0, i)      # el rótulo va en su propia línea
    return salto if salto >= 0 else i


def partir_cola(cuerpo: str, llano: str | None = None):
    """(cuerpo limpio, cola) o None. El espacio en blanco que separaba ambos
    se va con la cola, no se tira: así cuerpo + cola devuelve el original
    carácter a carácter y --deshacer es exacto."""
    i = corte(llano if llano is not None else _plano(cuerpo), len(cuerpo))
    if i is None:
        return None
    j = len(cuerpo[:i].rstrip())
    return cuerpo[:j], cuerpo[j:]


def main(argv=None) -> int:
    # Sin esto la salida no aparece hasta el final cuando va por una tubería,
    # y un proceso largo parece colgado.
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser(description="Cortar la cola de relacionados")
    ap.add_argument("--escribir", action="store_true",
                    help="sin esto, solo dice lo que haría")
    ap.add_argument("--deshacer", action="store_true",
                    help="devuelve al cuerpo las colas ya cortadas")
    ap.add_argument("--muestras", type=int, default=5)
    args = ap.parse_args(argv)

    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        print(aviso)
        return 2

    with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
        cur.execute("""ALTER TABLE articles
                       ADD COLUMN IF NOT EXISTS trimmed_tail TEXT""")
        conn.commit()

        if args.deshacer:
            cur.execute("""SELECT count(*) FROM articles
                            WHERE trimmed_tail IS NOT NULL""")
            n = cur.fetchone()[0]
            if not args.escribir:
                print(f"Devolvería su cola a {n} análisis. "
                      f"Añade --escribir para hacerlo.")
                return 0
            cur.execute("""UPDATE articles
                              SET body = body || trimmed_tail,
                                  trimmed_tail = NULL
                            WHERE trimmed_tail IS NOT NULL""")
            conn.commit()
            print(f"Devueltas {n} colas.")
            return 0

        cur.execute("""
            SELECT a.id, p.slug,
                   CASE WHEN EXTRACT(YEAR FROM a.published_date) >= 2021
                        THEN 'desde 2021' ELSE 'hasta 2020' END,
                   a.title, a.body
              FROM articles a JOIN publications p ON p.id = a.publication_id
             WHERE a.body IS NOT NULL AND a.trimmed_tail IS NULL
             ORDER BY a.id
        """)
        filas = cur.fetchall()

        cortes: list[tuple[int, str, str]] = []   # (id, cuerpo nuevo, cola)
        por_grupo: dict[tuple[str, str], list[int]] = {}
        protegidos = 0
        muestras: list[tuple[str, str, str]] = []
        otros: dict[str, int] = {}
        for art_id, slug, epoca, titulo, cuerpo in filas:
            llano = _plano(cuerpo)
            for r in OTROS_ROTULOS:
                if r in llano:
                    otros[r] = otros.get(r, 0) + 1
            if ROTULO not in llano:
                continue
            partido = partir_cola(cuerpo, llano)
            if partido is None:
                protegidos += 1
                continue
            nuevo, cola = partido
            cortes.append((art_id, nuevo, cola))
            por_grupo.setdefault((slug, epoca), []).append(len(cola))
            if len(muestras) < args.muestras:
                muestras.append((titulo or "", nuevo[-70:], cola.strip()))

        print("=" * 66)
        print("COLA DE «ARTÍCULOS RELACIONADOS»")
        print("=" * 66)
        if not cortes:
            print("\nNo hay nada que cortar.")
            return 0

        print(f"\n{'publicación':18} {'época':11} {'análisis':>9} "
              f"{'chars a quitar':>15} {'mediana':>8}")
        total = 0
        for (slug, epoca), largos in sorted(por_grupo.items()):
            largos.sort()
            total += sum(largos)
            print(f"{slug:18} {epoca:11} {len(largos):9} "
                  f"{sum(largos):15,} {largos[len(largos)//2]:8}")
        print(f"\n  {len(cortes)} análisis · {total:,} caracteres en total")
        if protegidos:
            print(f"  {protegidos} llevan el rótulo en la primera mitad del "
                  f"texto: no se tocan")
        if otros:
            print("\n  Otros rótulos de navegación que también aparecen "
                  "(no se cortan):")
            for r, n in sorted(otros.items(), key=lambda x: -x[1]):
                print(f"    {n:5}  «{r}»")

        ids = [c[0] for c in cortes]
        cur.execute("""SELECT count(*) FROM article_expectations e
                        WHERE e.article_id = ANY(%s)""", (ids,))
        citas = cur.fetchone()[0]
        perdidas = 0
        if citas:
            colas = {c[0]: c[2] for c in cortes}
            cur.execute("""SELECT article_id, quote FROM article_expectations
                            WHERE article_id = ANY(%s)""", (ids,))
            for art_id, cita in cur.fetchall():
                if _plano(cita) in _plano(colas[art_id]):
                    perdidas += 1
        print(f"  {citas} citas verificadas cuelgan de esos análisis; "
              f"{perdidas} vivían dentro de la cola")

        print("\n--- QUÉ SE CORTARÍA ---")
        for titulo, antes, cola in muestras:
            print(f"\n  {titulo[:60]}")
            print(f"    se queda: …{antes.strip()}")
            for linea in cola.split("\n"):
                if linea.strip():
                    print(f"    se va:    {linea.strip()[:66]}")

        if not args.escribir:
            print("\n  (ensayo en seco: no se ha escrito nada)")
            print("  Añade --escribir para cortar. Es reversible con "
                  "--deshacer --escribir.")
            return 0

        # Una UPDATE por fila son 2.595 idas y venidas a Railway, y cada una
        # rehace el tsvector y el índice GIN: media hora larga. En un solo
        # UPDATE contra una tabla de valores es un viaje y un barrido.
        cur.execute("""
            UPDATE articles a
               SET body = v.cuerpo, trimmed_tail = v.cola
              FROM (SELECT * FROM unnest(%s::int[], %s::text[], %s::text[])
                      AS t(id, cuerpo, cola)) v
             WHERE a.id = v.id
        """, ([c[0] for c in cortes], [c[1] for c in cortes],
              [c[2] for c in cortes]))
        tocadas = cur.rowcount
        conn.commit()
        print(f"\n  Cortados {tocadas} análisis. "
              f"La búsqueda se reindexa sola (tsv es GENERATED).")
        if tocadas != len(cortes):
            print(f"  Ojo: esperaba {len(cortes)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
