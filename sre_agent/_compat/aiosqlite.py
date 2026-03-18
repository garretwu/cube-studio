"""Tiny aiosqlite-compatible wrapper backed by sqlite3 for local tests."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()

    async def close(self) -> None:
        self._cursor.close()


class Connection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        return Cursor(self._conn.execute(sql, tuple(params)))

    async def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Cursor:
        return Cursor(self._conn.executemany(sql, [tuple(params) for params in seq_of_params]))

    async def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    async def commit(self) -> None:
        self._conn.commit()

    async def rollback(self) -> None:
        self._conn.rollback()

    async def close(self) -> None:
        self._conn.close()


async def connect(path: str, **kwargs: Any) -> Connection:
    conn = sqlite3.connect(path, **kwargs)
    return Connection(conn)
