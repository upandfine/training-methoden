-- Run once at first container start.
-- Safe to re-run: all statements use IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- fuzzy/ILIKE/similarity on names + synonyms
CREATE EXTENSION IF NOT EXISTS "unaccent";    -- diacritic-insensitive search (Ö, Ä, Ü, ß)
CREATE EXTENSION IF NOT EXISTS "vector";      -- pgvector: embeddings for later semantic search

-- German FTS config with unaccent + stemming.
-- Do nothing if it already exists (CREATE TEXT SEARCH CONFIGURATION has no IF NOT EXISTS).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_ts_config WHERE cfgname = 'german_unaccent'
  ) THEN
    CREATE TEXT SEARCH CONFIGURATION german_unaccent ( COPY = german );
    ALTER TEXT SEARCH CONFIGURATION german_unaccent
      ALTER MAPPING FOR hword, hword_part, word
      WITH unaccent, german_stem;
  END IF;
END
$$;
