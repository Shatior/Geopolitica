"""Carga de config.yaml y variables de entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PARSED_DIR = DATA_DIR / "parsed"
DEBUG_DIR = DATA_DIR / "debug"
STATE_FILE = DATA_DIR / "state.json"

load_dotenv(ROOT / ".env")


@dataclass
class Publication:
    slug: str
    name: str
    first_year: int
    archive_slug: str
    category_slug: str
    issue_url_hints: list[str] = field(default_factory=list)


@dataclass
class Config:
    base_url: str
    throttle: dict
    publications: dict[str, Publication]
    article_url_patterns: list[str]
    exclude_path_prefixes: list[str]

    # entorno
    cookies_file: str | None = None
    username: str | None = None
    password: str | None = None
    database_url: str | None = None


def load_config(path: Path | None = None) -> Config:
    raw = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    pubs = {
        slug: Publication(slug=slug, **p) for slug, p in raw["publications"].items()
    }
    return Config(
        base_url=raw["base_url"].rstrip("/"),
        throttle=raw.get("throttle", {}),
        publications=pubs,
        article_url_patterns=raw.get("article_url_patterns", []),
        exclude_path_prefixes=raw.get("exclude_path_prefixes", []),
        cookies_file=os.getenv("PE_COOKIES_FILE") or None,
        username=os.getenv("PE_USERNAME") or None,
        password=os.getenv("PE_PASSWORD") or None,
        database_url=os.getenv("DATABASE_URL") or None,
    )


def ensure_dirs() -> None:
    for d in (RAW_DIR, PARSED_DIR, DEBUG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def aviso_database_url(url: str | None) -> str | None:
    """Devuelve un aviso si la URL de la base de datos no sirve desde fuera de
    Railway. La variable DATABASE_URL de Railway apunta a un host interno
    (*.railway.internal) que solo resuelve dentro de su red: desde tu PC o
    desde un runner de GitHub hay que usar DATABASE_PUBLIC_URL."""
    if not url:
        return ("Falta DATABASE_URL. En Railway, servicio Postgres → Variables → "
                "copia DATABASE_PUBLIC_URL (la pública, no la interna).")
    if ".railway.internal" in url and not os.getenv("RAILWAY_ENVIRONMENT"):
        return ("DATABASE_URL apunta al host interno de Railway "
                "(*.railway.internal), que solo resuelve dentro de Railway. "
                "Desde tu PC o desde GitHub Actions usa DATABASE_PUBLIC_URL "
                "(la de *.proxy.rlwy.net).")
    return None


def url_interna_de_railway(url: str | None) -> bool:
    return bool(url) and ".railway.internal" in url
