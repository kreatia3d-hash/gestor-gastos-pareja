"""
Capa de base de datos: SQLite (local) o PostgreSQL (Railway/producción).
Importar get_db() desde aquí en lugar de usar sqlite3 directamente.
"""
import os
import re
import sqlite3

DATABASE_URL = os.environ.get('DATABASE_URL')
_db_path: str = ''


def set_db_path(path: str):
    global _db_path
    _db_path = path


# ── Transformación SQL SQLite → PostgreSQL ────────────────────────────────────

_STR_RE  = re.compile(r"strftime\('%Y-%m',\s*([\w.]+)\)", re.I)
_STR_Y   = re.compile(r"strftime\('%Y',\s*([\w.]+)\)", re.I)
_OR_IGN  = re.compile(r'INSERT\s+OR\s+IGNORE\s+INTO', re.I)
_PRAGMA  = re.compile(r'^\s*PRAGMA\b', re.I)


def _pg_sql(sql: str):
    """Devuelve (sql_pg, needs_returning, is_conflict_nothing)."""
    if _PRAGMA.match(sql):
        return 'SELECT 1', False, False

    is_or_ignore  = bool(_OR_IGN.search(sql))
    is_plain_ins  = bool(re.match(r'^\s*INSERT\s+INTO\b', sql, re.I))
    is_insert     = is_or_ignore or is_plain_ins

    # OR IGNORE → ON CONFLICT DO NOTHING
    if is_or_ignore:
        sql = _OR_IGN.sub('INSERT INTO', sql)
        sql = sql.rstrip('; ') + ' ON CONFLICT DO NOTHING'

    # Placeholders
    sql = sql.replace('?', '%s')

    # Funciones de fecha
    sql = _STR_RE.sub(r"TO_CHAR(\1::date, 'YYYY-MM')", sql)
    sql = _STR_Y.sub(r"TO_CHAR(\1::date, 'YYYY')", sql)

    # RETURNING id para INSERTs normales (no OR IGNORE)
    needs_returning = is_plain_ins and 'RETURNING' not in sql.upper()
    if needs_returning:
        sql = sql.rstrip('; ') + ' RETURNING id'

    return sql, needs_returning, is_or_ignore


# ── Cursor PostgreSQL ─────────────────────────────────────────────────────────

class _PgRow(dict):
    pass


class _PgCursor:
    def __init__(self, cur, returning: bool):
        self._cur = cur
        self._lastrowid = None
        if returning:
            row = cur.fetchone()
            self._lastrowid = row['id'] if row else None

    @property
    def lastrowid(self):
        return self._lastrowid

    def fetchone(self):
        r = self._cur.fetchone()
        return _PgRow(r) if r else None

    def fetchall(self):
        return [_PgRow(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return self

    def __next__(self):
        r = self._cur.fetchone()
        if r is None:
            raise StopIteration
        return _PgRow(r)


class _PgConn:
    def __init__(self):
        import psycopg2
        import psycopg2.extras
        # Railway añade "postgres://" pero psycopg2 necesita "postgresql://"
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        self._conn = psycopg2.connect(
            url,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def execute(self, sql: str, params=()):
        sql_pg, returning, _ = _pg_sql(sql)
        cur = self._conn.cursor()
        cur.execute(sql_pg, params if params else ())
        return _PgCursor(cur, returning)

    def cursor(self):
        import psycopg2.extras
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── Cursor SQLite ─────────────────────────────────────────────────────────────

class _SqliteRow(dict):
    def __init__(self, row):
        super().__init__(dict(row))


class _SqliteCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def fetchone(self):
        r = self._cur.fetchone()
        return _SqliteRow(r) if r else None

    def fetchall(self):
        return [_SqliteRow(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return self

    def __next__(self):
        r = self._cur.fetchone()
        if r is None:
            raise StopIteration
        return _SqliteRow(r)


class _SqliteConn:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA foreign_keys = ON')

    def execute(self, sql: str, params=()):
        return _SqliteCursor(self._conn.execute(sql, params))

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── Punto de entrada ──────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        return _PgConn()
    return _SqliteConn(_db_path)
