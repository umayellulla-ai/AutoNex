import sqlite3
import threading
from pathlib import Path

import mysql.connector

from config import Config


BASE_DIR = Path(__file__).resolve().parent
DB_BACKEND = None
DB_BACKEND_LOCK = threading.Lock()


def connect_sqlite():
    conn = sqlite3.connect(str(BASE_DIR / "autonex.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn, "sqlite"


def connect_mysql():
    conn = mysql.connector.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
        autocommit=True,
        connection_timeout=max(1, int(Config.DB_CONNECTION_TIMEOUT_SECONDS)),
    )
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    cursor.close()
    return conn, "mysql"


def get_db_connection():
    global DB_BACKEND

    if DB_BACKEND == "sqlite":
        return connect_sqlite()
    if DB_BACKEND == "mysql":
        try:
            return connect_mysql()
        except Exception:
            if not Config.USE_SQLITE_FALLBACK:
                raise
            with DB_BACKEND_LOCK:
                DB_BACKEND = "sqlite"
            return connect_sqlite()

    with DB_BACKEND_LOCK:
        if DB_BACKEND is None:
            try:
                conn, _ = connect_mysql()
                DB_BACKEND = "mysql"
                return conn, "mysql"
            except Exception:
                if not Config.USE_SQLITE_FALLBACK:
                    raise
                DB_BACKEND = "sqlite"
        return connect_sqlite()


def convert_row(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return row


def _execute(query, params=(), fetch_mode=None):
    conn, db_type = get_db_connection()
    try:
        if db_type == "sqlite":
            cursor = conn.cursor()
        else:
            query = query.replace("?", "%s")
            cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        if fetch_mode == "all":
            result = cursor.fetchall()
            return [convert_row(row) for row in result] if db_type == "sqlite" else result
        if fetch_mode == "one":
            result = cursor.fetchone()
            return convert_row(result) if db_type == "sqlite" else result
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def db_fetch_all(query, params=()):
    return _execute(query, params, "all")


def db_fetch_one(query, params=()):
    return _execute(query, params, "one")


def db_execute(query, params=()):
    return _execute(query, params)