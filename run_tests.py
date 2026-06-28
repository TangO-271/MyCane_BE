import sys
import pytest
import io

print("Starting pytest run")
try:
    with open('pytest_result.txt', 'w', encoding='utf-8') as f:
        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = f
        sys.stderr = f
        
        exit_code = pytest.main(["tests", "-v", "--tb=short"])
        
        # Restore
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        
    print(f"Pytest finished with exit code {exit_code}")
except Exception as e:
    print(f"Exception during pytest: {e}")
