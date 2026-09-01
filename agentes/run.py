"""Ejecuta las lentes de análisis sobre el corpus y publica su informe.

    python -m agentes.run                          # todas las lentes
    python -m agentes.run --lente senales
    python -m agentes.run --publicacion informe-semanal --escala semestre

De momento el informe se imprime; nada se escribe en la base de datos ni se
llama a ningún modelo de lenguaje. El objetivo de esta fase es ver qué tipo de
hallazgos produce el método antes de decidir cómo se publican.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import cronista, senales
from .base import cargar_corpus, conectar


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lentes de análisis del archivo")
    ap.add_argument("--lente", choices=["cronista", "senales", "todas"], default="todas")
    ap.add_argument("--publicacion", default=None, help="slug; por defecto, todas")
    ap.add_argument("--escala", choices=["anio", "semestre"], default="anio")
    ap.add_argument("--desde", type=int, default=0, help="año inicial del corpus")
    ap.add_argument("--ventana", type=int, default=4, help="periodos de la ventana de señales")
    ap.add_argument("--corte", default=None, help="periodo de corte para la retrospectiva")
    args = ap.parse_args(argv)

    url = conectar()
    t0 = time.time()
    c = cargar_corpus(url, publicacion=args.publicacion, desde=args.desde,
                      escala=args.escala)
    print(f"Corpus: {len(c.analisis)} análisis · {len(c.periodos)} periodos "
          f"({c.periodos[0]}–{c.periodos[-1]}) · {len(c.df_total)} términos "
          f"· {time.time()-t0:.1f}s")
    if args.publicacion:
        print(f"Publicación: {args.publicacion}")
    if c.descartados:
        print(f"Excluidos por no tener el texto completo: {c.descartados} "
              f"análisis (no entran en ningún recuento)")

    # Salud del corpus: sin esto no se puede distinguir un cambio de agenda de
    # un cambio en la longitud o la calidad de extracción de los textos.
    print("\nSALUD DEL CORPUS (lo que hay que mirar antes de creerse una tendencia)")
    print(f"  {'periodo':10} {'análisis':>9} {'términos/análisis':>18} "
          f"{'factor de escala':>17}")
    for p in c.periodos[-8:]:
        f = c.escala.get(p, 1.0)
        aviso = "  ← desvía las cuotas" if f < 0.7 or f > 1.4 else ""
        print(f"  {p:10} {c.docs_por_periodo[p]:9} {c.densidad.get(p, 0):18.0f} "
              f"{f:17.2f}{aviso}")
    print("  El factor resume cuánto infla ese periodo la cuota de cualquier "
          "término.\n  Las lentes dividen por él; las cuotas mostradas son las "
          "crudas.")
    print()

    if args.lente in ("cronista", "todas"):
        print(cronista.informe(c, cronista.analizar(c)))
        print()

    if args.lente in ("senales", "todas"):
        corte = args.corte or (c.periodos[-4] if len(c.periodos) > 6 else None)
        val = senales.retrospectiva(c, corte, ventana=args.ventana) if corte else None
        print(senales.informe(c, senales.detectar(c, ventana=args.ventana), val))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
