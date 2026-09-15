#!/bin/bash
# Runs once when the PostgreSQL data directory is created.
# Creates the pgvector extension and the restricted application role.
# Table-level grants for the role are applied by Alembic migrations.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE EXTENSION IF NOT EXISTS vector;
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
            CREATE ROLE "${APP_DB_USER}" LOGIN PASSWORD '${APP_DB_PASSWORD}';
        END IF;
    END
    \$\$;
    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${APP_DB_USER}";
    GRANT USAGE ON SCHEMA public TO "${APP_DB_USER}";
SQL
