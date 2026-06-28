import sys
from unittest.mock import MagicMock

# Mock apscheduler to prevent background threads from hanging pytest
scheduler_mock = MagicMock()
sys.modules['apscheduler'] = scheduler_mock
sys.modules['apscheduler.schedulers'] = scheduler_mock
sys.modules['apscheduler.schedulers.background'] = scheduler_mock

# Mock psycopg2 to prevent ThreadedConnectionPool from hanging if DB is down
psycopg2_mock = MagicMock()
pool_mock = MagicMock()
psycopg2_mock.pool = pool_mock
psycopg2_mock.pool.ThreadedConnectionPool = MagicMock()
sys.modules['psycopg2'] = psycopg2_mock
sys.modules['psycopg2.pool'] = pool_mock

# Also mock sqlalchemy so that tests that use it won't hang connecting to DB
sys.modules['sqlalchemy'] = MagicMock()
