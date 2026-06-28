import sys
import threading
from unittest.mock import MagicMock

# Import real psycopg2 error classes before mocking the module
import psycopg2 as _real_pg

# Mock apscheduler to prevent background threads from hanging pytest
scheduler_mock = MagicMock()
sys.modules['apscheduler'] = scheduler_mock
sys.modules['apscheduler.schedulers'] = scheduler_mock
sys.modules['apscheduler.schedulers.background'] = scheduler_mock


# Minimal stub for psycopg2.pool.ThreadedConnectionPool so that
# BlockingThreadedConnectionPool (which inherits from it) can be instantiated
# without a real DB.  Each getconn() returns a mock connection whose cursor
# returns empty result sets — enough for the tile renderer to produce valid
# 256×256 empty PNGs rather than crashing.
class _FakeThreadedPool:
    def __init__(self, *args, **kwargs):
        pass

    def getconn(self, key=None):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.__enter__ = lambda s: cur
        cur.__exit__ = lambda s, *a: None
        conn.cursor.return_value = cur
        conn.autocommit = False
        return conn

    def putconn(self, conn, key=None, close=False):
        pass

    def closeall(self):
        pass


pool_mock = MagicMock()
pool_mock.ThreadedConnectionPool = _FakeThreadedPool

psycopg2_mock = MagicMock()
psycopg2_mock.pool = pool_mock
# Preserve real error classes so exception handlers in main.py still work
psycopg2_mock.Error = _real_pg.Error
psycopg2_mock.OperationalError = _real_pg.OperationalError

sys.modules['psycopg2'] = psycopg2_mock
sys.modules['psycopg2.pool'] = pool_mock
