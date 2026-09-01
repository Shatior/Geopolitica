"""Cimientos comunes de las lentes de análisis.

Regla de oro del proyecto: **los números los calcula el código, nunca el
modelo**. Este módulo carga el corpus, lo trocea en periodos y cuenta
términos; las lentes se limitan a interpretar esas cuentas. Cuando más
adelante intervenga un modelo de lenguaje, recibirá cifras ya calculadas y
tendrá prohibido añadir ninguna.

Decisiones metodológicas que condicionan todo lo demás:

* Se cuenta **frecuencia documental** (en cuántos análisis aparece un
  término), no repeticiones: un artículo obsesionado con una palabra no
  distorsiona la serie.
* Todo se expresa como **cuota** (análisis con el término ÷ análisis del
  periodo). Imprescindible: el corpus pasa de ~45 análisis al año antes de
  2021 a ~240 después, y en frecuencias brutas todo parecería estallar ahí.
* Los **bigramas no cruzan puntuación**: «…el comercio. La cumbre…» no puede
  producir el término fantasma «comercio cumbre».
* Los resultados se **desduplican por solapamiento**: si «corredor ártico»,
  «corredor» y «ártico» señalan los mismos análisis, solo sobrevive el más
  informativo.
* Las cuotas se **corrigen por densidad del periodo**. Un análisis más largo
  contiene más términos distintos, así que la cuota de *cualquier* palabra
  sube con la longitud media de los textos. Si un año los análisis son más
  extensos —o se extrajeron mejor—, todo el vocabulario parece ganar
  atención a la vez. Se calcula un factor de escala por periodo (la mediana
  del cociente de cada término frecuente respecto a su propia media
  geométrica) y se divide por él: el ruido común se va, la señal se queda.
* Los titulares se guardan **por periodo**. Preguntar si un término «ya fue
  titular» solo puede mirar hacia atrás; usar el archivo entero filtraría
  del pasado justo los términos que triunfaron después.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.config import aviso_database_url, load_config  # noqa: E402

# Palabras vacías y genéricas del castellano periodístico: aparecen en casi
# todos los análisis y no distinguen unos temas de otros.
VACIAS = set("""
a al algo algun alguna algunas alguno algunos ambos ante antes aquel aquella
aquellas aquello aquellos aqui asi aun aunque bajo bien cada casi caso cierta
ciertas cierto ciertos como con contra cual cuales cualquier cuando cuanto cuyo
de del demas desde despues donde dos durante e el ella ellas ello ellos en
entonces entre era eran es esa esas ese eso esos esta estaba estan estar estas
este esto estos fin fue fueron gran grande grandes ha habia han hasta hay hecho
incluso ja junto la las le les lo los luego mas mayor mayores me media medio
mejor menos mientras mismo misma mismas mismos mucha muchas mucho muchos muy
nada ni ningun ninguna no nos nuestra nuestro nueva nuevas nuevo nuevos nunca
o otra otras otro otros para parte pero pesar poco pocos podria por porque
posible primer primera primero pudo pueda puede pueden pues que queda quien
quienes se sea sean segun segunda segundo ser si sido siempre sin sino sobre
solo son su sus tal tambien tampoco tan tanto tener tenia tiene tienen toda
todas todo todos tras tres tuvo un una unas uno unos vez veces y ya
ano anos actual actualmente ademas ahora ambas anterior aparte apenas
cosa cosas dado dar debe deben decir dice dicho dio ejemplo estado estados
forma formas hacer hace hacia hoy lado lugar manera modo momento nivel
niveles parece pasado proceso punto respecto resulta seria sera sigue
situacion tema temas tiempo tipo vista mismo pueda haber sera
""".split())

MIN_DF_GLOBAL = 4          # términos con menos apariciones no interesan a nadie

_RE_PALABRA = re.compile(r"[a-záéíóúüñ]{4,}", re.IGNORECASE)
_RE_FRASE = re.compile(r"[.;:!?¡¿()\[\]«»\"\n]+")


def sin_acentos(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c))


def terminos(texto: str) -> set[str]:
    """Términos distintos de un texto: palabras y pares de palabras contiguas.

    Los bigramas («mar rojo», «corredor artico») son lo que de verdad nombra un
    asunto geopolítico. Se forman dentro de cada frase, nunca cruzando un punto
    o un paréntesis, para no inventar expresiones que el autor no escribió.
    """
    out: set[str] = set()
    for frase in _RE_FRASE.split(texto or ""):
        palabras = [sin_acentos(p.lower()) for p in _RE_PALABRA.findall(frase)]
        for i, p in enumerate(palabras):
            if p in VACIAS:
                continue
            out.add(p)
            if i + 1 < len(palabras) and palabras[i + 1] not in VACIAS:
                out.add(f"{p} {palabras[i + 1]}")
    return out


@dataclass
class Analisis:
    id: int
    fecha: date
    titulo: str
    periodo: str
    terminos_titulo: set[str] = field(default_factory=set)
    texto: str = ""


@dataclass
class Corpus:
    analisis: list[Analisis]
    periodos: list[str]
    docs_por_periodo: dict[str, int]
    df: dict[str, dict[str, int]]             # término -> periodo -> nº de análisis
    df_total: dict[str, int]
    en_titulares: set[str]
    titulares: dict[str, set[str]] = field(default_factory=dict)
    densidad: dict[str, float] = field(default_factory=dict)
    escala: dict[str, float] = field(default_factory=dict)
    descartados: int = 0                      # análisis sin texto completo

    def cuota(self, termino: str, periodo: str) -> float:
        n = self.docs_por_periodo.get(periodo, 0)
        return self.df.get(termino, {}).get(periodo, 0) / n if n else 0.0

    def cuota_n(self, termino: str, periodo: str) -> float:
        """Cuota corregida por la densidad léxica del periodo. Es la que deben
        usar las lentes: comparar cuotas crudas entre periodos de distinta
        longitud media atribuye a la actualidad lo que solo es extensión."""
        return self.cuota(termino, periodo) / self.escala.get(periodo, 1.0)

    def titulares_hasta(self, periodo: str) -> set[str]:
        """Términos que ya habían sido titular en o antes de ese periodo."""
        out: set[str] = set()
        for p in self.periodos:
            if p <= periodo:
                out |= self.titulares.get(p, set())
        return out

    def docs_de(self, candidatos: set[str]) -> dict[str, set[int]]:
        """Qué análisis contienen cada uno de estos términos. Se calcula solo
        para los candidatos finalistas: guardarlo para todo el vocabulario
        ocuparía varios gigabytes."""
        out: dict[str, set[int]] = {t: set() for t in candidatos}
        for a in self.analisis:
            for t in terminos(a.texto) & candidatos:
                out[t].add(a.id)
        return out

    def ejemplos(self, termino: str, n: int = 3) -> list[Analisis]:
        halla = [a for a in self.analisis if termino in terminos(a.texto)]
        return halla[-n:]


def desduplicar(candidatos: list[dict], docs: dict[str, set[int]],
                clave: str = "termino", solape: float = 0.8) -> list[dict]:
    """Elimina los términos que señalan el mismo fenómeno que otro ya aceptado.

    Dos términos son redundantes cuando uno aparece casi siempre donde el otro
    («corredor ártico» y «ártico»). Se conserva el que llega antes en la lista,
    que viene ordenada por relevancia y prefiere los bigramas por ser más
    específicos.
    """
    aceptados: list[dict] = []
    conjuntos: list[set[int]] = []
    for cand in candidatos:
        s = docs.get(cand[clave], set())
        if not s:
            continue
        if any(len(s & otro) / min(len(s), len(otro)) >= solape for otro in conjuntos):
            continue
        aceptados.append(cand)
        conjuntos.append(s)
    return aceptados


def especificidad(termino: str, df_total: dict[str, int]) -> int:
    """Cuán común es la palabra más común del término. Menor es mejor: entre
    «abre rutas» y «corredor ártico», que señalan lo mismo, gana el segundo
    porque «abre» aparece en medio archivo y «corredor» casi en ninguno."""
    return max((df_total.get(w, 0) for w in termino.split()), default=0)


def factores_escala(df: dict[str, dict[str, int]], docs: dict[str, int],
                    periodos: list[str], minimo_df: int = 40) -> dict[str, float]:
    """Cuánto infla o desinfla cada periodo la cuota de un término cualquiera.

    Mismo problema que el tamaño de librería en secuenciación, y misma
    solución: se toman los términos frecuentes presentes en todos los
    periodos, se divide su cuota en cada periodo por su media geométrica, y
    se resume el periodo con la **mediana** de esos cocientes. La mediana
    ignora a los pocos términos que de verdad cambiaron de importancia, así
    que lo que queda es el efecto técnico: longitud media de los textos,
    calidad de la extracción, cambios de estilo.

    Un factor de 3 significa que ese periodo hace parecer tres veces más
    presente a *todo* el vocabulario; sin corregirlo, cualquier lente
    confundiría ese salto con un cambio de agenda.
    """
    from math import exp, log
    if len(periodos) < 2:
        return {p: 1.0 for p in periodos}

    cocientes: dict[str, list[float]] = {p: [] for p in periodos}
    for termino, por_periodo in df.items():
        if sum(por_periodo.values()) < minimo_df:
            continue
        cuotas = [por_periodo.get(p, 0) / docs[p] if docs.get(p) else 0.0 for p in periodos]
        if any(q <= 0 for q in cuotas):        # debe existir en todos, o no compara
            continue
        media_geom = exp(sum(log(q) for q in cuotas) / len(cuotas))
        for p, q in zip(periodos, cuotas):
            cocientes[p].append(q / media_geom)

    def mediana(xs: list[float]) -> float:
        if not xs:
            return 1.0
        xs = sorted(xs)
        m = len(xs) // 2
        return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2

    n_base = len(cocientes[periodos[0]])
    if n_base < 200:            # muestra insuficiente: mejor no corregir nada
        return {p: 1.0 for p in periodos}
    return {p: max(mediana(v), 1e-6) for p, v in cocientes.items()}


def cola_binomial(n: int, total: int, prob: float, superior: bool = True) -> float:
    """Probabilidad de ver al menos (o como mucho) n análisis con el término,
    si en realidad nada hubiera cambiado.

    Sin esta prueba, con decenas de miles de términos examinados siempre habrá
    unos cuantos que se desvíen mucho por puro azar, y la lente los presentaría
    como hallazgos. Es el error que delató el corpus sintético al señalar
    «delegacion bilateral», dos palabras de relleno juntas por casualidad.
    """
    from math import exp, lgamma, log
    prob = min(max(prob, 1e-12), 1 - 1e-12)
    if total <= 0:
        return 1.0

    def log_pmf(k: int) -> float:
        return (lgamma(total + 1) - lgamma(k + 1) - lgamma(total - k + 1)
                + k * log(prob) + (total - k) * log(1 - prob))

    rango = range(n, total + 1) if superior else range(0, n + 1)
    mayor = max(log_pmf(k) for k in rango)
    return min(1.0, exp(mayor) * sum(exp(log_pmf(k) - mayor) for k in rango))


def clave_periodo(f: date, escala: str) -> str:
    if escala == "semestre":
        return f"{f.year}-S{1 if f.month <= 6 else 2}"
    return str(f.year)


def cargar_corpus(database_url: str, publicacion: str | None = None,
                  desde: int = 0, escala: str = "anio",
                  solo_completos: bool = True) -> Corpus:
    """Lee los análisis y cuenta términos por periodo, en dos pasadas: la
    primera descarta el vocabulario anecdótico para que la segunda quepa
    holgadamente en memoria."""
    filtros = ["a.kind <> 'portada'", "a.published_date IS NOT NULL"]
    params: dict = {}
    if solo_completos:
        filtros.append("a.is_full")
    if publicacion:
        filtros.append("p.slug = %(pub)s")
        params["pub"] = publicacion
    if desde:
        filtros.append("EXTRACT(YEAR FROM a.published_date) >= %(desde)s")
        params["desde"] = desde

    with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT a.id, a.published_date, a.title, coalesce(a.body,'') AS body
                FROM articles a JOIN publications p ON p.id = a.publication_id
                WHERE {' AND '.join(filtros)}
                ORDER BY a.published_date""",
            params,
        )
        filas = cur.fetchall()

        descartados = 0
        if solo_completos:
            cur.execute(
                f"""SELECT count(*) AS n
                    FROM articles a JOIN publications p ON p.id = a.publication_id
                    WHERE {' AND '.join(x for x in filtros if x != 'a.is_full')}
                      AND NOT a.is_full""",
                params,
            )
            descartados = cur.fetchone()["n"]

    return corpus_desde_filas(filas, escala, descartados)


def corpus_desde_filas(filas, escala: str = "anio", descartados: int = 0) -> Corpus:
    """Cuenta términos por periodo. Separada de la consulta para poder probar
    el método con corpus sintéticos, sin base de datos."""
    analisis: list[Analisis] = []
    docs: Counter = Counter()
    totales: Counter = Counter()
    en_titulares: set[str] = set()
    titulares: dict[str, set[str]] = defaultdict(set)

    for f in filas:
        per = clave_periodo(f["published_date"], escala)
        tt = terminos(f["title"] or "")
        texto = (f["title"] or "") + ". " + f["body"]
        analisis.append(Analisis(id=f["id"], fecha=f["published_date"],
                                 titulo=f["title"] or "", periodo=per,
                                 terminos_titulo=tt, texto=texto))
        docs[per] += 1
        en_titulares |= tt
        titulares[per] |= tt
        totales.update(terminos(texto))

    vivos = {t for t, n in totales.items() if n >= MIN_DF_GLOBAL}
    del totales

    df: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    suma_terminos: Counter = Counter()
    for a in analisis:
        propios = terminos(a.texto)
        suma_terminos[a.periodo] += len(propios)
        for t in propios & vivos:
            df[t][a.periodo] += 1

    periodos = sorted(docs)
    df = {t: dict(v) for t, v in df.items()}
    return Corpus(
        analisis=analisis, periodos=periodos, docs_por_periodo=dict(docs),
        df=df,
        df_total={t: sum(v.values()) for t, v in df.items()},
        en_titulares=en_titulares,
        titulares={p: set(v) for p, v in titulares.items()},
        densidad={p: suma_terminos[p] / docs[p] for p in periodos},
        escala=factores_escala(df, dict(docs), periodos),
        descartados=descartados,
    )


def conectar() -> str:
    cfg = load_config()
    aviso = aviso_database_url(cfg.database_url)
    if aviso:
        raise SystemExit(aviso)
    return cfg.database_url


def barra(v: float, maximo: float, ancho: int = 20) -> str:
    return "█" * max(0, round(v * ancho / max(maximo, 1e-9)))
