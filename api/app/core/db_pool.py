import threading
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
import app.core.state as state
from pipeline.config import DATABASE_URL


class BlockingThreadedConnectionPool(ThreadedConnectionPool):
    """psycopg2 pool that blocks (rather than raises) when all connections are in use."""

    def __init__(self, minconn, maxconn, *args, **kwargs):
        super().__init__(minconn, maxconn, *args, **kwargs)
        self._semaphore = threading.Semaphore(maxconn)

    def getconn(self, key=None):
        self._semaphore.acquire()
        try:
            return super().getconn(key)
        except Exception:
            self._semaphore.release()
            raise

    def putconn(self, conn, key=None, close=False):
        try:
            super().putconn(conn, key, close)
        finally:
            self._semaphore.release()


def get_raw_db():
    """FastAPI dependency — yields a psycopg2 connection from the blocking pool."""
    if state.db_pool is None:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = state.db_pool.getconn()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            state.db_pool.putconn(conn)
