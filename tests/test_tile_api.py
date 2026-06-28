import sys
from pathlib import Path
from fastapi.testclient import TestClient
import mercantile
from PIL import Image
import io

sys.path.append(str(Path(__file__).parent.parent))
from api.main import app

def test_tile_api_endpoints():
    client = TestClient(app)
    
    # Coordinates of demo plot center: 100.26, 16.82
    tile = mercantile.tile(100.26, 16.82, 13)
    z, x, y = tile.z, tile.x, tile.y
    
    print(f"\n--- Testing Tile API on Tile: {z}/{x}/{y} ---")
    
    layers = ["ndvi", "ndmi", "nbr", "hotspot", "burn_scar"]
    for layer in layers:
        url = f"/api/v1/tiles/{layer}/{z}/{x}/{y}.png"
        print(f"Requesting GET {url}...")
        resp = client.get(url)
        
        assert resp.status_code == 200, f"Failed on {layer}: status code is {resp.status_code}"
        assert resp.headers["content-type"] == "image/png", f"Failed on {layer}: content-type is {resp.headers.get('content-type')}"
        
        # Verify PNG signature
        content = resp.content
        assert content.startswith(b"\x89PNG\r\n\x1a\n"), f"Failed on {layer}: invalid PNG signature"
        
        # Verify image dimensions
        img = Image.open(io.BytesIO(content))
        assert img.size == (256, 256), f"Failed on {layer}: invalid image size {img.size}"
        assert img.format == "PNG", f"Failed on {layer}: invalid format {img.format}"
        print(f"[OK] Layer '{layer}' rendered successfully!")

    # Specifically test disease layer with user_id parameter
    disease_url_with_user = f"/api/v1/tiles/disease/{z}/{x}/{y}.png?user_id=1"
    print(f"Requesting GET {disease_url_with_user}...")
    resp = client.get(disease_url_with_user)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    content = resp.content
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    img = Image.open(io.BytesIO(content))
    assert img.size == (256, 256)
    print("[OK] Layer 'disease' with user_id query param rendered successfully!")

if __name__ == "__main__":
    test_tile_api_endpoints()
