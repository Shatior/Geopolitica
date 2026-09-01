"""Cronista de entidades: quién sube, quién desaparece, quién es nuevo.

Misma idea que el cronista de vocabulario, pero contando lo que el
enriquecimiento identificó —actores, lugares y temas— en lugar de palabras
sueltas. El cambio no es cosmético; resuelve tres problemas de golpe:

* **Ya no hace falta corregir la densidad.** Un análisis recibe entre tres y
  ocho entidades por categoría sea largo o corto, así que la longitud del texto
  deja de inflar las cuentas. La corrección seguía siendo necesaria con
  vocabulario porque un texto más largo contiene más palabras distintas.
* **La corrección por comparaciones múltiples deja de ser asfixiante.** Se
  examinan cientos de entidades en vez de veinte mil, así que el umbral de
  significación es mucho menos exigente y sobreviven hallazgos reales que antes
  se perdían.
* **Lo que sale tiene nombre.** «Nayib Bukele» o «populismo punitivo» son una
  pieza; «mediante» no lo era.

Regla intacta: el modelo nombró, el código cuenta. Aquí no interviene ninguno.

Cautela propia de esta lente: las cuotas se calculan **solo sobre los análisis
enriquecidos** de cada periodo. Mientras el enriquecimiento esté a medias, un
periodo con pocos análisis procesados daría porcentajes engañosos, así que el
informe muestra siempre la cobertura y avisa cuando es baja.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from .base import clave_periodo, cola_binomial  # noqa: E402

SOPORTE_MIN = 4        # análisis mínimos con la entidad en el periodo actual
BASE_MIN = 3           # periodos mínimos de historia
LIFT_MIN = 1.6
ALFA = 0.01
COBERTURA_MIN = 0.5    # por debajo de esto, el periodo no es representativo

PLURAL = {"actor": "actores", "lugar": "lugares", "tema": "temas"}


def cargar(database_url: str, publicacion: str | None = None,
           escala: str = "anio", desde: int = 0) -> dict:
    """Entidades por periodo, y cuántos análisis hay detrás de cada cuenta."""
    filtros = ["a.kind <> 'portada'", "a.published_date IS NOT NULL"]
    params: dict = {}
    if publicacion:
        filtros.append("p.slug = %(pub)s")
        params["pub"] = publicacion
    if desde:
        filtros.append("EXTRACT(YEAR FROM a.published_date) >= %(desde)s")
        params["desde"] = desde
    donde = " AND ".join(filtros)

    with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        # Denominadores: analizados y enriquecidos, por periodo.
        cur.execute(
            f"""SELECT a.published_date, a.enriched_at IS NOT NULL AS rico
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE {donde} AND a.is_full""", params)
        total, ricos = defaultdict(int), defaultdict(int)
        for f in cur.fetchall():
            per = clave_periodo(f["published_date"], escala)
            total[per] += 1
            if f["rico"]:
                ricos[per] += 1

        cur.execute(
            f"""SELECT e.kind, e.name, a.published_date
                FROM article_entities e
                JOIN articles a ON a.id = e.article_id
                JOIN publications p ON p.id = a.publication_id
                WHERE {donde}""", params)
        df: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for f in cur.fetchall():
            per = clave_periodo(f["published_date"], escala)
            df[(f["kind"], f["name"])][per] += 1

    return {
        "periodos": sorted(total),
        "total": dict(total), "ricos": dict(ricos),
        "df": {k: dict(v) for k, v in df.items()},
    }


def analizar(datos: dict, periodos_base: int = 5, top: int = 10) -> dict:
    periodos = [p for p in datos["periodos"] if datos["ricos"].get(p, 0) > 0]
    if len(periodos) < BASE_MIN + 1:
        return {"suficiente": False,
                "motivo": "hacen falta al menos cuatro periodos enriquecidos"}

    actual = periodos[-1]
    base = periodos[-(periodos_base + 1):-1]
    if len(base) < BASE_MIN:
        return {"suficiente": False, "motivo": "base histórica corta"}

    n_actual = datos["ricos"][actual]
    examinadas = sum(1 for v in datos["df"].values() if sum(v.values()) >= SOPORTE_MIN)
    umbral = ALFA / max(examinadas, 1)

    def cuota(clave, per):
        n = datos["ricos"].get(per, 0)
        return datos["df"][clave].get(per, 0) / n if n else 0.0

    suben, bajan, nuevas = [], [], []
    for clave, por_periodo in datos["df"].items():
        kind, name = clave
        n_ahora = por_periodo.get(actual, 0)
        c_ahora = cuota(clave, actual)
        c_base = [cuota(clave, p) for p in base]
        media = sum(c_base) / len(c_base)

        # Nuevas: no existían en ningún periodo anterior al actual.
        antes = sum(v for p, v in por_periodo.items() if p < actual)
        if antes == 0 and n_ahora >= SOPORTE_MIN:
            nuevas.append({"kind": kind, "name": name, "n": n_ahora, "cuota": c_ahora})
            continue

        if media <= 0:
            continue
        esperada = min(media, 0.999)
        if n_ahora >= SOPORTE_MIN and c_ahora / media >= LIFT_MIN:
            if cola_binomial(n_ahora, n_actual, esperada, True) <= umbral:
                suben.append({"kind": kind, "name": name, "n": n_ahora,
                              "cuota": c_ahora, "base": media,
                              "lift": c_ahora / media, "delta": c_ahora - media})
        media_n = sum(por_periodo.get(p, 0) for p in base) / len(base)
        if media_n >= SOPORTE_MIN and c_ahora / media <= 1 / LIFT_MIN:
            if cola_binomial(n_ahora, n_actual, esperada, False) <= umbral:
                bajan.append({"kind": kind, "name": name, "n": n_ahora,
                              "cuota": c_ahora, "base": media,
                              "lift": c_ahora / media, "delta": media - c_ahora})

    suben.sort(key=lambda d: -d["delta"])
    bajan.sort(key=lambda d: -d["delta"])
    nuevas.sort(key=lambda d: -d["n"])
    cobertura = n_actual / max(datos["total"].get(actual, 0), 1)
    return {
        "suficiente": bool(suben or bajan or nuevas),
        "periodo": actual, "base": f"{base[0]}–{base[-1]}",
        "n_actual": n_actual, "cobertura": cobertura, "examinadas": examinadas,
        "suben": suben[:top], "bajan": bajan[:top], "nuevas": nuevas[:top],
    }


def informe(res: dict) -> str:
    if not res.get("suficiente"):
        return f"ENTIDADES — sin hallazgos ({res.get('motivo', 'nada supera el umbral')})."

    out = [
        "=" * 70,
        f"CRONISTA DE ENTIDADES · {res['periodo']} frente a {res['base']}",
        "=" * 70,
        f"{res['n_actual']} análisis enriquecidos en {res['periodo']} "
        f"({res['cobertura']*100:.0f}% de los del periodo) · "
        f"{res['examinadas']} entidades examinadas",
    ]
    if res["cobertura"] < COBERTURA_MIN:
        out.append("AVISO: menos de la mitad del periodo está enriquecido; "
                   "las cuotas no son representativas todavía.")
    out.append("")

    def bloque(titulo, filas, signo):
        out.append(titulo)
        if not filas:
            out.append("  (ninguna supera el umbral)")
        for d in filas:
            out.append(f"  {signo}{abs(d['delta'])*100:4.1f} pt  "
                       f"{PLURAL[d['kind']][:7]:8} {d['name'][:34]:36} "
                       f"{d['n']:3} análisis ({d['cuota']*100:4.1f}%)")
        out.append("")

    bloque("▲ MÁS PRESENTES QUE DE COSTUMBRE", res["suben"], "+")
    bloque("▼ MENOS PRESENTES QUE DE COSTUMBRE", res["bajan"], "−")

    out.append("★ APARECEN POR PRIMERA VEZ EN EL ARCHIVO")
    if not res["nuevas"]:
        out.append("  (ninguna con soporte suficiente)")
    for d in res["nuevas"]:
        out.append(f"  {PLURAL[d['kind']][:7]:8} {d['name'][:34]:36} "
                   f"{d['n']:3} análisis ({d['cuota']*100:4.1f}%)")
    return "\n".join(out)
