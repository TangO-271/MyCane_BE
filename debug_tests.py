import sys
from pathlib import Path

def log(msg):
    with open('debug_log.txt', 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

log("Starting debug_tests.py")

try:
    log("Importing sys, pathlib...")
    import sys
    sys.path.append(str(Path(__file__).parent))
    
    log("Importing app.core.config (from tests.test_api_contract)...")
    from api.app.core.config import get_db
    log("Importing api.main...")
    from api import main as api_main
    log("Import successful.")
    
    log("Creating TestClient...")
    from fastapi.testclient import TestClient
    log("TestClient imported.")
    
    # Try to instantiate TestClient to see if it hangs
    log("Instantiating TestClient(api_main.app)...")
    client = TestClient(api_main.app)
    log("TestClient instantiated successfully.")
    
except Exception as e:
    log(f"Exception: {e}")

log("Done.")
