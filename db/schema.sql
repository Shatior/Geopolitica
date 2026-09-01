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

CREATE INDEX IF NOT EXISTS idx_articles_tsv       ON articles USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_articles_tags      ON articles USING GIN (tags);
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
