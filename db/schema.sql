-- Esquema para PostgreSQL en Railway.
-- Diseñado para análisis de tendencias: relacional (publicación → número →
-- artículo), búsqueda de texto completo en español (tsvector + GIN) e
-- índices por fecha y etiquetas.

CREATE TABLE IF NOT EXISTS publications (
    id      SERIAL PRIMARY KEY,
    slug    TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    id              SERIAL PRIMARY KEY,
    publication_id  INT NOT NULL REFERENCES publications(id),
    url             TEXT UNIQUE NOT NULL,
    number          INT,
    title           TEXT,
    published_date  DATE,
    pdf_url         TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,
    publication_id  INT NOT NULL REFERENCES publications(id),
    issue_id        INT REFERENCES issues(id),
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    subtitle        TEXT,
    authors         TEXT[] NOT NULL DEFAULT '{}',
    tags            TEXT[] NOT NULL DEFAULT '{}',
    published_date  DATE,
    body            TEXT,
    is_full         BOOLEAN NOT NULL DEFAULT FALSE,
    raw_html_path   TEXT,
    scraped_at      TIMESTAMPTZ,
    -- Búsqueda de texto completo en español sobre título + subtítulo + cuerpo
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(subtitle, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(body, '')), 'C')
    ) STORED
);

-- Marca de que ya se intentó rescatar el texto desde el PDF del número,
-- para no volver a descargarlo en cada ejecución.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS pdf_rescued_at TIMESTAMPTZ;

-- Distingue los análisis de las portadas de número de la revista bimestral,
-- que viven bajo /articulo/ pero solo contienen el sumario del bimestre.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'articulo';
UPDATE articles SET kind = 'portada'
 WHERE kind <> 'portada'
   AND url ~ '/articulo/([a-z0-9-]*-)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)-?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)-(19|20)[0-9]{2}/?$';

CREATE INDEX IF NOT EXISTS idx_articles_tsv       ON articles USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_articles_tags      ON articles USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_articles_kind      ON articles (kind);
CREATE INDEX IF NOT EXISTS idx_articles_date      ON articles (published_date);
CREATE INDEX IF NOT EXISTS idx_articles_pub_date  ON articles (publication_id, published_date);
CREATE INDEX IF NOT EXISTS idx_issues_pub_number  ON issues (publication_id, number);

-- Ejemplos de consultas de tendencias:
--
-- Menciones de "Sahel" por trimestre:
--   SELECT date_trunc('quarter', published_date) AS q, count(*)
--   FROM articles
--   WHERE tsv @@ websearch_to_tsquery('spanish', 'Sahel')
--   GROUP BY q ORDER BY q;
--
-- Autores más prolíficos de un año:
--   SELECT unnest(authors) AS autor, count(*)
--   FROM articles
--   WHERE published_date BETWEEN '2023-01-01' AND '2023-12-31'
--   GROUP BY autor ORDER BY count(*) DESC LIMIT 20;

-- ---------------------------------------------------------------------------
-- Enriquecimiento: lo que un modelo de lenguaje extrae de cada análisis.
--
-- Las lentes estadísticas miden bien nombres propios («Biden», «Ormuz») pero se
-- enturbian con vocabulario suelto, porque una palabra no es un actor ni un
-- tema. Aquí se guarda esa capa: quién actúa, dónde, sobre qué, y qué se
-- afirmó sobre el futuro. El modelo solo nombra; contar sigue siendo cosa del
-- código.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS article_entities (
    article_id  INT  NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,          -- actor | lugar | tema
    name        TEXT NOT NULL,
    PRIMARY KEY (article_id, kind, name)
);

-- Afirmaciones sobre el futuro, citadas literalmente del análisis. Alimentan
-- al auditor de pronósticos: solo se guardan las que se han podido verificar
-- palabra por palabra contra el cuerpo del texto.
CREATE TABLE IF NOT EXISTS article_expectations (
    id          SERIAL PRIMARY KEY,
    article_id  INT  NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    quote       TEXT NOT NULL,
    UNIQUE (article_id, quote)
);

-- Un lote de la Batch API tarda hasta 24 horas, más de lo que dura una
-- ejecución cómoda: se anota aquí para poder recoger el resultado más tarde,
-- incluso desde otra máquina.
CREATE TABLE IF NOT EXISTS enrichment_batches (
    id           TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    model        TEXT NOT NULL,
    n_requests   INT  NOT NULL,
    collected_at TIMESTAMPTZ,
    n_ok         INT,
    n_error      INT
);

ALTER TABLE articles ADD COLUMN IF NOT EXISTS enriched_at      TIMESTAMPTZ;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS enrichment_model TEXT;

CREATE INDEX IF NOT EXISTS idx_entities_kind_name ON article_entities (kind, name);
CREATE INDEX IF NOT EXISTS idx_entities_article   ON article_entities (article_id);
CREATE INDEX IF NOT EXISTS idx_articles_enriched  ON articles (enriched_at);

-- Unificar variantes del mismo nombre. El modelo escribe unas veces «estrecho
-- de Ormuz» y otras «Estrecho de Ormuz», y las lentes las contaban como dos
-- entidades distintas: 17 análisis por un lado y 11 por otro en lugar de 28.
-- Se agrupa ignorando mayúsculas y acentos, y gana la forma más frecuente.
--
-- Primero se borran las filas que chocarían: un análisis que mencione las dos
-- variantes acabaría con dos filas idénticas, y la clave primaria lo impide.
DELETE FROM article_entities e
 USING (SELECT DISTINCT ON (kind, clave) kind, clave, name AS forma
          FROM (SELECT kind, name,
                       lower(translate(name, 'áéíóúüñÁÉÍÓÚÜÑ',
                                             'aeiouunaeiouun')) AS clave,
                       count(*) OVER (PARTITION BY kind, name) AS veces
                  FROM article_entities) n
         ORDER BY kind, clave, veces DESC, name) c
 WHERE e.kind = c.kind
   AND lower(translate(e.name, 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunaeiouun')) = c.clave
   AND e.name <> c.forma
   AND EXISTS (SELECT 1 FROM article_entities x
                WHERE x.article_id = e.article_id
                  AND x.kind = e.kind AND x.name = c.forma);

UPDATE article_entities e
   SET name = c.forma
  FROM (SELECT DISTINCT ON (kind, clave) kind, clave, name AS forma
          FROM (SELECT kind, name,
                       lower(translate(name, 'áéíóúüñÁÉÍÓÚÜÑ',
                                             'aeiouunaeiouun')) AS clave,
                       count(*) OVER (PARTITION BY kind, name) AS veces
                  FROM article_entities) n
         ORDER BY kind, clave, veces DESC, name) c
 WHERE e.kind = c.kind
   AND lower(translate(e.name, 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunaeiouun')) = c.clave
   AND e.name <> c.forma;

-- Etiquetas derivadas de las entidades, que son la fuente de verdad. Se
-- recalculan siempre, no solo cuando faltan, para que la unificación de arriba
-- se refleje también en lo que muestra la web.
UPDATE articles a
   SET tags = sub.etiquetas
  FROM (SELECT article_id,
               (array_agg(DISTINCT name))[1:10] AS etiquetas
          FROM article_entities
         WHERE kind IN ('tema', 'lugar')
         GROUP BY article_id) sub
 WHERE a.id = sub.article_id
   AND a.tags IS DISTINCT FROM sub.etiquetas;
