import pytest
import numpy as np
import rasterio
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent))
from pipeline.config import PROCESSED_DIR

def check_index_range(file_path, index_name):
    """ฟังก์ชันย่อยตรวจสอบว่าข้อมูลอยู่ในช่วง -1.0 ถึง 1.0"""
    with rasterio.open(file_path) as src:
        data = src.read(1)
        valid_data = data[~np.isnan(data)]
        
        if len(valid_data) > 0:
            assert np.min(valid_data) >= -1.0, f"{index_name} min value < -1.0 (Found {np.min(valid_data)})"
            assert np.max(valid_data) <= 1.0, f"{index_name} max value > 1.0 (Found {np.max(valid_data)})"

def test_indices_ranges():
    """ตรวจสอบว่าค่า NDVI, NDMI, NBR อยู่ในช่วง -1.0 ถึง 1.0 เสมอ"""
    indices_dir = PROCESSED_DIR / "indices"
    if not indices_dir.exists():
        pytest.skip("Indices directory not found")
        
    scene_dirs = [d for d in indices_dir.iterdir() if d.is_dir()]
    if not scene_dirs:
        pytest.skip("No computed index scenes found")
        
    for scene in scene_dirs:
        for idx in ["NDVI", "NDMI", "NBR"]:
            idx_file = scene / f"{scene.name}_{idx}.tif"
            if idx_file.exists():
                check_index_range(idx_file, idx)

def test_tiff_properties():
    """ตรวจสอบว่าไฟล์ tiff ที่ประมวลผลมีความละเอียดสมจริงตามหน้างาน"""
    indices_dir = PROCESSED_DIR / "indices"
    if not indices_dir.exists():
        pytest.skip("Indices directory not found")
        
    scene_dirs = [d for d in indices_dir.iterdir() if d.is_dir()]
    if not scene_dirs:
        pytest.skip("No computed index scenes found")
        
    for scene in scene_dirs:
        ndvi_file = scene / f"{scene.name}_NDVI.tif"
        if ndvi_file.exists():
            with rasterio.open(ndvi_file) as src:
                # Mock resolution was set to 0.0001
                res_x, res_y = src.res
                assert res_x <= 0.0002, f"Pixel size too large for x: {res_x}"
                assert res_y <= 0.0002, f"Pixel size too large for y: {res_y}"
