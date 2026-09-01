"""Qué se le pide al modelo y cómo se comprueba lo que devuelve.

Principio del proyecto, aquí también: **el modelo nombra, el código cuenta**.
Haiku no ve cifras, no compara periodos y no opina sobre tendencias; se limita
a leer un análisis y decir quién actúa, dónde, sobre qué, y qué se afirma sobre
el futuro. Todo recuento posterior lo hace PostgreSQL.

Las citas prospectivas se **verifican palabra por palabra** contra el cuerpo
del artículo antes de guardarlas. Una cita que el modelo haya reescrito, por
fiel que suene, se descarta: el auditor de pronósticos solo puede trabajar
sobre lo que el autor escribió de verdad.
"""
from __future__ import annotations

import json
import re
import unicodedata

MODELO = "claude-haiku-4-5"
MAX_TOKENS = 1200

SISTEMA = """Eres un documentalista de un archivo de análisis geopolítico en \
español. Lees un análisis y lo catalogas. No interpretas, no valoras y no \
resumes: identificas.

Reglas:
- Nombra en español y en forma canónica: «Estados Unidos», no «EEUU» ni «los \
estadounidenses»; «Unión Europea», no «Bruselas» cuando se refiere a la UE.
- Un actor tiene agencia: Estados, organizaciones, gobiernos, empresas, \
dirigentes con nombre. No son actores los conceptos ni los sectores.
- Un lugar es el escenario de lo que se narra, no toda mención de pasada.
- Un tema es un asunto analítico de dos a cuatro palabras («disuasión \
nuclear», «control de estrechos»), nunca una palabra suelta y genérica \
(«política», «crisis», «relaciones»).
- Incluye solo lo que vertebra el análisis. Es preferible dejar fuera algo \
dudoso que inflar la lista: entre tres y ocho elementos por categoría basta.
- En «expectativas», copia LITERALMENTE, carácter por carácter, frases del \
texto que afirmen algo sobre el futuro (previsiones, riesgos anunciados, \
condiciones para que algo ocurra). Sin parafrasear, sin corregir, sin unir \
trozos separados. Si el análisis no afirma nada sobre el futuro, deja la \
lista vacía."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "actores": {"type": "array", "items": {"type": "string"}},
        "lugares": {"type": "array", "items": {"type": "string"}},
        "temas": {"type": "array", "items": {"type": "string"}},
        "expectativas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["actores", "lugares", "temas", "expectativas"],
    "additionalProperties": False,
}

CATEGORIAS = {"actores": "actor", "lugares": "lugar", "temas": "tema"}


def peticion(titulo: str, cuerpo: str) -> dict:
    """Los parámetros de la llamada, sin el envoltorio del lote."""
    return {
        "model": MODELO,
        "max_tokens": MAX_TOKENS,
        "system": SISTEMA,
        "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}},
        "messages": [{
            "role": "user",
            "content": f"TÍTULO: {titulo}\n\nTEXTO:\n{cuerpo}",
        }],
    }


def _plano(s: str) -> str:
    """Texto comparable: sin acentos, sin comillas tipográficas y con los
    espacios normalizados. pypdf y el HTML del sitio difieren en esos detalles
    y no queremos descartar una cita buena por una comilla curva."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def interpretar(texto_json: str, cuerpo: str) -> dict | None:
    """Convierte la respuesta en entidades y citas verificadas.

    Devuelve None si la respuesta no es utilizable. Cada cita que no aparezca
    literalmente en el cuerpo se descarta y se contabiliza aparte: es la única
    defensa contra que el modelo mejore la prosa del autor.
    """
    try:
        datos = json.loads(texto_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(datos, dict):
        return None

    entidades: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    for campo, clase in CATEGORIAS.items():
        for bruto in datos.get(campo) or []:
            if not isinstance(bruto, str):
                continue
            nombre = re.sub(r"\s+", " ", bruto).strip(" .,;:")
            if not 2 <= len(nombre) <= 80:
                continue
            clave = (clase, nombre.lower())
            if clave in vistos:
                continue
            vistos.add(clave)
            entidades.append((clase, nombre))

    cuerpo_plano = _plano(cuerpo)
    citas, descartadas = [], 0
    for bruto in datos.get("expectativas") or []:
        if not isinstance(bruto, str):
            continue
        cita = re.sub(r"\s+", " ", bruto).strip()
        if len(cita) < 30:            # demasiado corta para ser una afirmación
            continue
        if _plano(cita) in cuerpo_plano:
            citas.append(cita)
        else:
            descartadas += 1

    return {"entidades": entidades, "citas": citas, "citas_descartadas": descartadas}
