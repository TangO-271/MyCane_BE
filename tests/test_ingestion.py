import pytest
import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from pipeline.config import RAW_DIR

def test_stac_search_results():
    """ตรวจสอบว่าไฟล์ search_results.json มีรูปแบบที่ถูกต้องตาม Validation Plan"""
    sentinel_dir = RAW_DIR / "sentinel2"
    search_files = list(sentinel_dir.glob("search_results_*.json"))
    
    if not search_files:
        pytest.skip("No STAC search results found to test")
        
    latest_search = max(search_files, key=lambda x: x.stat().st_mtime)
    
    with open(latest_search, 'r') as f:
        results = json.load(f)
        
    assert len(results) > 0, "Search results should not be empty"
    
    for item in results:
        # Check Cloud Cover (must be < 80%)
        cloud_cover = item.get('cloud_cover')
        if cloud_cover != "N/A":
            assert float(cloud_cover) < 80.0, f"Cloud cover {cloud_cover}% exceeds 80% threshold"
        
        # Ensure essential keys exist
        assert 'id' in item
        assert 'datetime' in item
        assert 'bbox' in item
        assert 'assets' in item
