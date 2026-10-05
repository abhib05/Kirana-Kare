import json
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, aliases TEXT DEFAULT '[]', unit TEXT DEFAULT 'pc',
  price REAL, stock REAL, reorder_level REAL DEFAULT 5);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT, phone TEXT, language TEXT DEFAULT 'hi',
  upi_names TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS baskets(
  id INTEGER PRIMARY KEY, ts REAL, status TEXT, lines TEXT, total REAL, payment TEXT,
  customer_id INTEGER, source_text TEXT, lang TEXT);
CREATE TABLE IF NOT EXISTS upi_alerts(
  id INTEGER PRIMARY KEY, ts REAL, amount REAL, payer TEXT, upi_ref TEXT, tag_ref TEXT,
  raw TEXT, app TEXT, status TEXT, basket_id INTEGER, confidence REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY, ts REAL, customer_id INTEGER, kind TEXT, amount REAL,
  basket_id INTEGER, ref TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS sale_lines(
  id INTEGER PRIMARY KEY, ts REAL, basket_id INTEGER, item_id INTEGER, qty REAL, amount REAL);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY, ts REAL, to_phone TEXT, to_name TEXT, kind TEXT, body TEXT, delivered INTEGER DEFAULT 0);
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()):
        with self.tx() as c:
            return c.execute(sql, args).lastrowid


def jl(s):
    return json.loads(s) if s else []
