"""Connections, and the one rule every write follows: name the agent first."""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

STORE_AGENT = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Database:
    def __init__(self, url: str):
        self.pool = ConnectionPool(url, kwargs={"row_factory": dict_row}, open=True, min_size=1, max_size=10)

    @contextmanager
    def read(self):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            yield conn

    @contextmanager
    def write(self, agent_id: uuid.UUID):
        """A transaction whose every write is attributed to `agent_id` by the audit trigger."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT set_config('lineage.agent_id', %s, true)", (str(agent_id),))
            yield conn

    def close(self):
        self.pool.close()
