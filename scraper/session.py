"""Sesión HTTP "educada": autenticación, cabeceras realistas, throttling y
reintentos con backoff. Todo el tráfico del scraper pasa por aquí, en una
única conexión secuencial — nunca en paralelo — para no disparar los
sistemas anti-bot del servidor.
"""
from __future__ import annotations

import logging
import random
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

log = logging.getLogger("scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

# Ante un 429/403 esperamos cada vez más antes de reintentar; si tras estas
# esperas sigue bloqueado, abortamos para no insistir contra el servidor.
BLOCK_BACKOFF = [120, 420, 900]


class ScraperBlocked(RuntimeError):
    """El servidor nos está rechazando de forma persistente."""


class PoliteSession:
    def __init__(self, base_url: str, throttle: dict):
        self.base_url = base_url
        self.min_delay = float(throttle.get("min_delay", 4.0))
        self.max_delay = float(throttle.get("max_delay", 9.0))
        self.long_every = int(throttle.get("long_pause_every", 25))
        self.long_min = float(throttle.get("long_pause_min", 60))
        self.long_max = float(throttle.get("long_pause_max", 150))

        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._n_requests = 0
        self._last_request_at = 0.0

    # ------------------------------------------------------------- auth
    def load_cookies(self, cookies_file: str) -> None:
        path = Path(cookies_file)
        if not path.exists():
            raise FileNotFoundError(
                f"No existe {path}. Exporta las cookies de tu navegador logueado "
                "(formato Netscape/cookies.txt) a esa ruta."
            )
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        for c in jar:
            self.session.cookies.set_cookie(c)
        log.info("Cargadas %d cookies desde %s", len(jar), path)

    def login(self, username: str, password: str) -> None:
        """Login estándar de WordPress. Si el sitio usa un formulario propio,
        usa la vía de cookies exportadas (PE_COOKIES_FILE)."""
        login_url = f"{self.base_url}/wp-login.php"
        # Primero un GET para recibir cookies previas al login
        self.get(f"{self.base_url}/", allow_cache=False)
        self._throttle()
        resp = self.session.post(
            login_url,
            data={
                "log": username,
                "pwd": password,
                "rememberme": "forever",
                "redirect_to": f"{self.base_url}/",
                "testcookie": "1",
            },
            headers={"Referer": login_url},
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()
        if not self.is_logged_in():
            raise RuntimeError(
                "El login no ha dejado cookie de sesión de WordPress. "
                "Usa la vía de cookies exportadas (PE_COOKIES_FILE en .env)."
            )
        log.info("Login correcto como %s", username)

    def is_logged_in(self) -> bool:
        return any(
            c.name.startswith("wordpress_logged_in") for c in self.session.cookies
        )

    # ------------------------------------------------------------- fetch
    def get(self, url: str, allow_cache: bool = True) -> requests.Response:
        """GET con throttling y manejo de bloqueos/errores transitorios."""
        for attempt, block_wait in enumerate([0] + BLOCK_BACKOFF):
            if block_wait:
                log.warning(
                    "Posible bloqueo (intento %d). Esperando %d s antes de reintentar…",
                    attempt, block_wait,
                )
                time.sleep(block_wait)
            self._throttle()
            try:
                resp = self.session.get(url, timeout=45)
            except requests.RequestException as exc:
                log.warning("Error de red en %s: %s. Reintentando…", url, exc)
                time.sleep(10 * (attempt + 1))
                continue

            if resp.status_code in (429, 403, 503):
                continue  # siguiente vuelta con espera larga
            return resp

        raise ScraperBlocked(
            f"El servidor sigue devolviendo bloqueo para {url} tras varios "
            "reintentos con esperas largas. Para el scraper y reanuda en unas horas "
            "(el estado se guarda y continuará donde lo dejó)."
        )

    def asomarse(self, url: str, max_bytes: int = 2048) -> tuple[int, dict, bytes]:
        """Pide un recurso y lee solo su comienzo, sin descargarlo entero.

        Pensado para comprobaciones: saber si detrás de un enlace hay un PDF o
        una página de aviso no exige bajarse veinte megas. Devuelve el código,
        las cabeceras y los primeros bytes.

        A diferencia de get(), **no reintenta ante un 403**: aquí un «prohibido»
        es la respuesta que buscamos (el sitio niega el documento), no un
        bloqueo del que haya que recuperarse esperando un cuarto de hora.
        """
        self._throttle()
        resp = self.session.get(url, timeout=45, stream=True)
        try:
            inicio = next(resp.iter_content(max_bytes), b"")
        finally:
            resp.close()
        return resp.status_code, dict(resp.headers), inicio

    def _throttle(self) -> None:
        self._n_requests += 1
        if self.long_every and self._n_requests % self.long_every == 0:
            pause = random.uniform(self.long_min, self.long_max)
            log.info(
                "Pausa larga de cortesía: %.0f s (petición nº %d)",
                pause, self._n_requests,
            )
            time.sleep(pause)
            return
        elapsed = time.monotonic() - self._last_request_at
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_at = time.monotonic()
