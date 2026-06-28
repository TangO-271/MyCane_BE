import sys
from unittest.mock import MagicMock

# Mock airflow and its submodules before importing the DAG
class DummyDAG:
    def __init__(self, *args, **kwargs):
        self.dag_id = kwargs.get('dag_id', args[0] if args else 'dummy')
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def __rshift__(self, other):
        return other

airflow_mock = MagicMock()
airflow_mock.DAG = DummyDAG

sys.modules['airflow'] = airflow_mock
sys.modules['airflow.operators'] = MagicMock()
sys.modules['airflow.operators.python'] = MagicMock()
sys.modules['airflow.operators.bash'] = MagicMock()

def test_dag_integrity():
    # Import the DAG module
    import dags.poc_pipeline_dag as dag_module
    
    # Assert that all 3 DAGs are defined and have correct IDs
    assert hasattr(dag_module, 'dag_hourly')
    assert hasattr(dag_module, 'dag_daily')
    assert hasattr(dag_module, 'dag_sentinel')
    
    assert dag_module.dag_hourly.dag_id == 'geoai_viirs_hotspot_hourly'
    assert dag_module.dag_daily.dag_id == 'geoai_weather_daily_briefing'
    assert dag_module.dag_sentinel.dag_id == 'geoai_sentinel2_processing'
