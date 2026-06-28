import sys
from pathlib import Path
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
import mercantile

sys.path.append(str(Path(__file__).parent.parent))
from api.main import app

def run_verification():
    print("=" * 60)
    print("📡 FastAPI Live Endpoints Verification Script")
    print("=" * 60)
    
    client = TestClient(app)
    
    # 1. GET /
    print("\n[1] Testing GET / (Root)")
    resp = client.get("/")
    print(f"Status: {resp.status_code}, Response: {resp.json()}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    
    # 2. GET /api/v1/features/PLT-001
    print("\n[2] Testing GET /api/v1/features/PLT-001")
    resp = client.get("/api/v1/features/PLT-001")
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Success! Returned plot_id: {data.get('plot_id')}")
        print(f"Indices: {data.get('indices')}")
        print(f"Weather (temp_max_c): {data.get('weather', {}).get('temp_max_c')}")
        print(f"Fire (burn_scar_recurrence): {data.get('fire', {}).get('burn_scar_recurrence')}")
        assert data.get('plot_id') == "PLT-001"
        assert "indices" in data
        assert "weather" in data
        assert "terrain" in data
        assert "fire" in data
        assert "soil" in data
        assert "spi" in data
    else:
        print(f"Failed! Detail: {resp.text}")
        assert False, f"Could not retrieve PLT-001 features: {resp.text}"
        
    # 3. GET /api/v1/features/PLT-001/history
    print("\n[3] Testing GET /api/v1/features/PLT-001/history")
    start_date = (datetime.now() - timedelta(days=30)).isoformat()
    end_date = (datetime.now() + timedelta(days=1)).isoformat()
    resp = client.get(
        "/api/v1/features/PLT-001/history",
        params={
            "start_date": start_date,
            "end_date": end_date,
            "indices": "ndvi,nbr"
        }
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Success! Series length: {len(data.get('series', []))}")
        print(f"First data point: {data.get('series', [])[0] if data.get('series') else 'None'}")
        assert data.get('plot_id') == "PLT-001"
        assert "series" in data
    else:
        print(f"Failed! Detail: {resp.text}")
        assert False, f"Could not retrieve history for PLT-001: {resp.text}"
        
    # 4. POST /api/v1/plots (Upsert plot)
    print("\n[4] Testing POST /api/v1/plots")
    plot_payload = {
        "plot_id": "PLT-099",
        "user_id": "USR-001",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [100.25, 16.81],
                [100.26, 16.81],
                [100.26, 16.82],
                [100.25, 16.82],
                [100.25, 16.81]
            ]]
        },
        "crop_type": "rice",
        "province": "phitsanulok"
    }
    resp = client.post("/api/v1/plots", json=plot_payload)
    print(f"Status: {resp.status_code}, Response: {resp.json()}")
    assert resp.status_code == 201
    assert resp.json()["status"] == "success"
    assert resp.json()["plot_id"] == "PLT-099"
    
    # 5. GET /api/v1/hotspots
    print("\n[5] Testing GET /api/v1/hotspots")
    resp = client.get(
        "/api/v1/hotspots",
        params={
            "bbox": "100.0,16.5,101.5,17.5",
            "hours": 168
        }
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Success! GeoJSON Type: {data.get('type')}")
        print(f"Total hotspots found: {len(data.get('features', []))}")
        assert data.get('type') == "FeatureCollection"
    else:
        print(f"Failed! Detail: {resp.text}")
        assert False, f"Could not retrieve hotspots: {resp.text}"
        
    # 6. GET /api/v1/tiles/{layer}/{z}/{x}/{y}.png
    print("\n[6] Testing Tile Rendering Endpoints")
    tile = mercantile.tile(100.26, 16.82, 13)
    z, x, y = tile.z, tile.x, tile.y
    layers = ["ndvi", "ndmi", "nbr", "hotspot", "burn_scar", "disease", "drought", "flood"]
    
    for layer in layers:
        url = f"/api/v1/tiles/{layer}/{z}/{x}/{y}.png"
        resp = client.get(url)
        print(f" - Layer '{layer}' tiles GET: status {resp.status_code}, length {len(resp.content)} bytes")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG header"
        
    # 7. Health Check
    print("\n[7] Testing GET /api/v1/health")
    resp = client.get("/api/v1/health")
    print(f"Status: {resp.status_code}, Response: {resp.json()}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # 8. User Auth Flow E2E (Supabase)
    print("\n[8] Testing User Registration & Login Flow")
    unique_email = f"test_{int(datetime.now().timestamp())}@heavenseye.com"
    register_payload = {
        "name": "Validation User",
        "email": unique_email,
        "password": "Password123!"
    }
    resp = client.post("/api/v1/auth/register", json=register_payload)
    print(f"Registration Status: {resp.status_code}")
    assert resp.status_code == 201
    
    # Login to get JWT
    login_payload = {
        "username": unique_email,
        "password": "Password123!"
    }
    resp = client.post("/api/v1/auth/login", data=login_payload)
    print(f"Login Status: {resp.status_code}")
    assert resp.status_code == 200
    token_data = resp.json()
    token = token_data["access_token"]
    assert token_data["token_type"] == "bearer"
    
    # Check /api/v1/auth/me
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    print(f"Auth Me Status: {resp.status_code}, User: {resp.json().get('email')}")
    assert resp.status_code == 200
    assert resp.json()["email"] == unique_email

    # 9. Plot E2E Operations with Auth (Supabase)
    print("\n[9] Testing Plot Registration with Authentication")
    plot_payload = {
        "plot_name": "Validation Plot 1",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [100.25, 16.81],
                [100.26, 16.81],
                [100.26, 16.82],
                [100.25, 16.82],
                [100.25, 16.81]
            ]]
        }
    }
    resp = client.post("/api/v1/plots/", json=plot_payload, headers={"Authorization": f"Bearer {token}"})
    print(f"Plot Creation Status: {resp.status_code}")
    assert resp.status_code == 201
    created_plot = resp.json()
    plot_id = created_plot["id"]
    assert created_plot["plot_name"] == "Validation Plot 1"
    
    # List plots
    resp = client.get("/api/v1/plots/", headers={"Authorization": f"Bearer {token}"})
    print(f"List Plots Status: {resp.status_code}, Count: {len(resp.json())}")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    
    # Upload test image
    print("\n[10] Testing Plot Image Upload")
    img_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82" # Tiny 1x1 transparent PNG
    import io
    file_payload = {"file": ("test.png", io.BytesIO(img_data), "image/png")}
    resp = client.post(
        f"/api/v1/plots/{plot_id}/upload-image",
        files=file_payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"Image Upload Status: {resp.status_code}")
    assert resp.status_code == 200
    assert resp.json()["image_url"] is not None
    print(f"Image URL: {resp.json()['image_url']}")
    
    # Send FCM/LINE Notification Broadcaster E2E
    print("\n[11] Testing Notification Sender Broadcast")
    notification_payload = {
        "message": "⚠️ Weather Warning: Impending heavy rainfall, secure your crops!",
        "target_users": [resp.json()["user_id"]]
    }
    resp = client.post(
        "/api/v1/notifications/send",
        json=notification_payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"Notification Broadcast Status: {resp.status_code}, Response: {resp.json()}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "success"

    # Delete the plot to clean up
    print("\n[12] Cleaning up created plot")
    resp = client.delete(f"/api/v1/plots/{plot_id}", headers={"Authorization": f"Bearer {token}"})
    print(f"Plot Delete Status: {resp.status_code}")
    assert resp.status_code == 200

    print("\n🎉 ALL unified contract endpoints verified successfully against real database!")
    print("=" * 60)

if __name__ == "__main__":
    run_verification()
