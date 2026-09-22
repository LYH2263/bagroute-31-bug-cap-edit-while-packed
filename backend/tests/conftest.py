import os

# Default tests to in-memory SQLite so no Postgres/psycopg2 is required.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
