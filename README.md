# Geopolítica — análisis de tendencias sobre Política Exterior

Pipeline para descargar (con tu suscripción de pago) los **Informes Semanales**
(desde 1995) y la **revista bimestral Política Exterior** (desde 1987) de
[politicaexterior.com](https://www.politicaexterior.com), y almacenarlos en una
base de datos **PostgreSQL en Railway** preparada para análisis de tendencias.

> Uso personal: el contenido es de pago y accedes con tu propia suscripción.
> No republiques los textos ni subas `data/` al repositorio (ya está en
> `.gitignore`).

## Arquitectura

```
politicaexterior.com ──> scraper/ ──> data/raw/*.html      (HTML crudo, backup)
     (tu sesión)                      data/parsed/*.jsonl  (registros extraídos)
                                            │
                                            ▼
                                      db/load.py ──> PostgreSQL (Railway)
```

- **Scraper educado**: secuencial, 4–9 s aleatorios entre peticiones, pausa
  larga cada ~25 peticiones, backoff ante 429/403/503 y aborto si el bloqueo
  persiste. Reanudable: si se corta, continúa donde lo dejó.
- **HTML crudo guardado**: si mañana quieres extraer otro campo, re-parseas en
  local sin volver a scrapear.
- **PostgreSQL** (y no Mongo/SQLite) porque el dominio es relacional
  (publicación → número → artículo), Railway lo ofrece gestionado con un clic,
  y trae búsqueda de texto completo en español (`tsvector`) que es justo lo
  que necesita un análisis de tendencias. Más adelante puedes añadir
  `pgvector` para embeddings sin cambiar de base de datos.

## Puesta en marcha (en tu máquina)

```bash
git clone <este repo> && cd Geopolitica
python -m venv .venv && source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. Autenticación (tu suscripción)

**Vía recomendada — cookies del navegador**: inicia sesión en
politicaexterior.com en Chrome/Firefox, exporta las cookies del dominio con una
extensión como *Get cookies.txt LOCALLY* (formato Netscape) y guárdalas en
`data/cookies.txt`. En `.env` ya apunta ahí `PE_COOKIES_FILE`.

**Vía alternativa**: pon `PE_USERNAME` y `PE_PASSWORD` en `.env` (login
estándar de WordPress). Si el sitio usa un formulario de login propio, esta vía
puede fallar; usa entonces las cookies.

Las cookies de WordPress caducan: si el scraper avisa de "artículos truncados",
vuelve a exportarlas y relanza (no repite lo ya descargado).

### 2. Probar con una página antes de lanzar nada

```bash
python -m scraper.inspect_page https://www.politicaexterior.com/archivo/informe-semanal-ano-2023/
```

Muestra qué detecta el parser (enlaces a números, artículos, cuerpo, si la
sesión está autenticada) y guarda el HTML en `data/debug/`. Si algún selector
no cuadra con el tema actual del sitio, se ajusta en `config.yaml`
(`article_url_patterns`, `issue_url_hints`) o en `scraper/parse.py`
(`BODY_SELECTORS`).

### 3. Scraping

```bash
# Ensayo pequeño: 1 año, 5 artículos
python -m scraper.run --publication informe-semanal --years 2023 --limit 5

# Todo el Informe Semanal (1995-hoy)
python -m scraper.run --publication informe-semanal

# Todo: semanal + revista bimestral (tardará horas; puedes cortar y reanudar)
python -m scraper.run
```

El progreso queda en `data/state.json`; relanzar nunca repite peticiones. Si el
servidor devolviera bloqueos persistentes, el scraper se detiene solo: espera
unas horas y reanuda.

Descubrimiento alternativo de artículos (por si el archivo anual cambia):
`--discover category` pagina `/categoria-articulo/<publicación>/page/N/`.

### 4. Base de datos en Railway

1. En [railway.app](https://railway.app): **New → Deploy PostgreSQL**.
2. En la pestaña *Variables* del Postgres, copia `DATABASE_PUBLIC_URL` y pégala
   como `DATABASE_URL` en tu `.env` local.
3. Carga (aplica el esquema y hace upsert, idempotente):

```bash
python -m db.load
```

Repite `scraper.run` + `db.load` cuando quieras incorporar los números nuevos
de cada semana (solo descargará lo que falte).

## Operar sin el PC: GitHub Actions contra la base de datos

Tras el backfill inicial, **la base de datos de Railway es la fuente de verdad**
y las operaciones se ejecutan en GitHub, no en local. El workflow
`.github/workflows/operaciones.yml` se lanza a mano desde **Actions →
operaciones → Run workflow** (o desde una sesión de Claude Code) y ofrece:

| operación | qué hace |
|---|---|
| `diagnostico` | informe de calidad de los datos; no modifica nada |
| `scrapear` | busca números nuevos, los carga en Railway y diagnostica |
| `pdfs` | rescata el texto de los números incompletos desde su PDF |

Parámetros: `publicacion`, `anyos` (p. ej. `2026` o `2024-2026`), `limite`
(0 = sin límite) y `reintentar` (solo en `pdfs`: vuelve a probar números ya
intentados, útil tras mejorar la extracción).

Secretos necesarios (**Settings → Secrets and variables → Actions**):

- `DATABASE_URL`: la **`DATABASE_PUBLIC_URL`** de Railway (la de
  `*.proxy.rlwy.net`). ⚠️ La variable `DATABASE_URL` que Railway muestra por
  defecto apunta a un host interno que solo resuelve dentro de Railway: sirve
  para el servicio web, pero no para GitHub ni para tu PC.
- `PE_COOKIES`: el contenido íntegro de tu `data/cookies.txt`.

Las operaciones que tocan la base (`pdfs --from-db`) aplican el esquema por su
cuenta y son reanudables: los artículos ya intentados quedan marcados en
`articles.pdf_rescued_at` y no se vuelven a descargar.

## Actualización semanal automática (GitHub Actions)

El backfill histórico se hace una vez desde tu PC; el mantenimiento lo hace
GitHub Actions: cada martes de madrugada `.github/workflows/informe-semanal.yml`
scrapea las novedades del año en curso (y el anterior, por el cambio de año) y
las carga en Railway. Gracias a `--skip-from-db` el runner no necesita estado
local: consulta a la base de datos qué URLs existen ya y solo baja lo nuevo
(~6 peticiones por semana).

Configuración (una vez): en GitHub, **Settings → Secrets and variables →
Actions → New repository secret**, crea:

- `PE_COOKIES`: pega el contenido completo de tu `data/cookies.txt`.
- `DATABASE_URL`: la `DATABASE_PUBLIC_URL` del Postgres en Railway.

Para probarlo sin esperar al martes: pestaña **Actions → informe-semanal →
Run workflow**.

Mantenimiento: la cookie de sesión caduca cada ~2 semanas. Si el job falla
con aviso de artículos truncados, reexporta las cookies del navegador y
actualiza el secreto `PE_COOKIES` (30 segundos). Alternativa 100% desatendida:
si el login automático funciona (prueba local con `PE_USERNAME`/`PE_PASSWORD`
en `.env`), usa esos dos secretos en lugar de la cookie y ajusta el workflow.

## Frontend: hemeroteca web (`web/`)

Interfaz para consultar lo que hay en la base de datos: listado con
buscador de texto completo en español (con resaltado), filtros por
publicación y año, y página de artículo con el texto íntegro y enlaces al
original y al PDF del número.

**En local:**

```bash
uvicorn web.app:app --reload
# http://127.0.0.1:8000
```

**Desplegada en Railway** (junto a la base de datos):

1. En el mismo proyecto de Railway: **New → GitHub Repo** y elige este repo
   (Railway detecta el `Procfile` y arranca `uvicorn` solo).
2. En *Variables* del nuevo servicio añade:
   - `DATABASE_URL` → referencia a la variable del Postgres: pulsa
     *Variable Reference* y elige `Postgres.DATABASE_URL` (la interna, sin
     coste de egress).
   - `WEB_USER` y `WEB_PASSWORD` → credenciales de acceso a la web.
3. En *Settings → Networking → Generate Domain* para obtener la URL pública.

⚠️ El contenido es de pago: no despliegues sin `WEB_PASSWORD` definida (sin
ella la app no pide contraseña) y no compartas el dominio.

## Rescatar los doce años que la web nunca publicó (`rescate/`)

Hasta 2020 el sitio publicaba **una sola sección de cada Informe Semanal**.
Medido contra el PDF del número 870 (16 dic 2013):

```
   3.259 caracteres  lo que guardamos de la web
  21.659 caracteres  las siete secciones del PDF
```

Las otras seis nunca estuvieron en internet, no están bajo otra URL y no se
recuperan scrapeando: existen solo dentro del PDF. Desde 2021 el sitio publica
el informe entero repartido en cinco piezas, así que esa época ya está
completa (el mismo cociente da 1,1×).

El rescate se lanza **desde tu máquina**, no desde GitHub: necesita la sesión
del sitio para bajar los PDF, y las descargas desde un centro de datos ya nos
costaron una sesión.

```bash
# 1. Bajar los PDF de los números que aún no lo tengan (necesita sesión)
python -m scraper.pdfs --from-db --publication informe-semanal

# 2. Ver qué saldría de un número, sin base de datos ni escrituras
python -m rescate.run --probar data/pdfs/informe-semanal-870.pdf

# 3. Ensayo en seco sobre todo lo que haya en disco
python -m rescate.run --simular

# 4. Cargar
python -m rescate.run
```

Qué hace con lo que ya existe: de cada número ya teníamos una pieza, que es
una de las secciones del PDF. **No se duplica ni se sustituye**: se empareja
por el arranque del texto y se conserva con su URL, sus etiquetas y su
enriquecimiento. Lo único que se le corrige es el titular, porque el guardado
es el del número («#ISPE 870. 16 diciembre 2013») y no el del texto («Crimen
–casi– sin castigo»). Las secciones restantes entran como piezas nuevas con
`kind = 'seccion-pdf'`.

El troceado no es heurístico: la maqueta usa familias tipográficas distintas
para el texto y para el mobiliario (sumario, cabecera, créditos, pies), y el
corte se hace por ahí. Se prueba sin PDF —que es material de pago y no se
versiona— con `python -m pruebas.secciones_sinteticas`.

Las secciones rescatadas entran **sin entidades ni citas verificadas**: para
que la lente de entidades las vea hay que pasarlas por
`python -m enriquecer.run` aparte.

## Esquema y consultas de tendencias

Tablas: `publications` → `issues` → `articles` (con `authors[]`, `tags[]`,
`published_date`, `body` y columna `tsv` de búsqueda en español). Ejemplos en
`db/schema.sql`; el clásico:

```sql
-- Evolución trimestral de menciones a un tema
SELECT date_trunc('quarter', published_date) AS trimestre, count(*)
FROM articles
WHERE tsv @@ websearch_to_tsquery('spanish', 'Sahel')
GROUP BY 1 ORDER BY 1;
```

## Estructura del repo

```
config.yaml            publicaciones, ritmo de scraping, patrones de URL
scraper/session.py     sesión HTTP educada (auth, throttling, backoff)
scraper/parse.py       extracción (JSON-LD, OpenGraph, selectores WP)
scraper/run.py         orquestador CLI reanudable
scraper/inspect_page.py  depuración de una página concreta
scraper/reparse.py     re-extrae desde el HTML ya descargado, sin volver a pedir
scraper/pdfs.py        descarga los PDF de cada número
rescate/secciones.py   parte el PDF de un informe en sus secciones
rescate/run.py         carga esas secciones y corrige los titulares genéricos
db/schema.sql          esquema PostgreSQL (FTS en español + índices)
db/load.py             carga idempotente de los JSONL a Railway
db/diag.py             informe de calidad de los datos
web/                   hemeroteca web (FastAPI + Jinja)
```
