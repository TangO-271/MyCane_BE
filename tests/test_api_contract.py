import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).parent.parent))

from api import main as api_main
from pipeline.zonal_stats import extract_stats as extract_stats_module


class FakeCursor:
    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row

    def close(self):
        return None


class FakeConnection:
    def __init__(self, *cursors):
        self._cursors = list(cursors)
        self.committed = False
        self.rolled_back = False
        self.autocommit = False

    def cursor(self):
        if not self._cursors:
            raise AssertionError("No cursor configured for fake connection")
        return self._cursors.pop(0)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


def make_plot_feature_row():
    # Columns match PLOT_FEATURE_SELECT_COLUMNS (row[0]–row[14])
    return (
        1,               # plot_id
        datetime(2026, 5, 19, 6, 0, 0),  # timestamp
        2,               # data_freshness_days
        12.5,            # cloud_cover_pct
        "high",          # confidence
        0.72,            # ndvi
        0.35,            # ndmi
        0.48,            # nbr
        45.2,            # rain_7d_mm
        78.5,            # humidity_pct
        12.3,            # wind_speed_kmh
        0,               # hotspot_count_24h
        2,               # hotspot_count_7d
        4.5,             # nearest_hotspot_km
        -0.45,           # spi_30d
    )


def test_get_latest_feature_returns_nested_contract():
    fake_conn = FakeConnection(FakeCursor(row=make_plot_feature_row()))

    def override_get_db():
        yield fake_conn

    api_main.app.dependency_overrides[api_main.get_db] = override_get_db
    client = TestClient(api_main.app)

    response = client.get("/api/v1/features/PLT-001")

    api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["plot_id"] == "PLT-001"
    assert payload["indices"] == {"ndvi": 0.72, "ndmi": 0.35, "nbr": 0.48}
    assert payload["weather"]["wind_speed_kmh"] == 12.3
    assert payload["weather"]["humidity_pct"] == 78.5
    assert payload["fire"]["hotspot_count_24h"] == 0
    assert payload["fire"]["nearest_hotspot_km"] == 4.5
    assert payload["spi"]["spi_30d"] == -0.45
    assert "terrain" not in payload
    assert "soil" not in payload


def test_get_feature_history_respects_requested_indices():
    rows = [
        (datetime(2026, 5, 10, 6, 0, 0), 0.61, 0.22, 0.31),
        (datetime(2026, 5, 15, 6, 0, 0), 0.72, 0.35, 0.48),
    ]
    fake_conn = FakeConnection(FakeCursor(rows=rows))

    def override_get_db():
        yield fake_conn

    api_main.app.dependency_overrides[api_main.get_db] = override_get_db
    client = TestClient(api_main.app)

    response = client.get(
        "/api/v1/features/PLT-001/history",
        params={
            "start_date": "2026-05-10T00:00:00",
            "end_date": "2026-05-20T00:00:00",
            "indices": "ndvi,nbr",
        },
    )

    api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["plot_id"] == "PLT-001"
    assert list(payload["series"][0].keys()) == ["timestamp", "ndvi", "nbr"]
    assert payload["series"][1]["ndvi"] == 0.72
    assert payload["series"][1]["nbr"] == 0.48


def test_create_plot_returns_created_identifier_and_uses_geojson_transform():
    fake_cursor = FakeCursor(row=(7,))
    fake_conn = FakeConnection(fake_cursor)

    def override_get_db():
        yield fake_conn

    api_main.app.dependency_overrides[api_main.get_db] = override_get_db
    client = TestClient(api_main.app)
    payload = {
        "plot_id": "PLT-007",
        "user_id": "USR-002",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [100.25, 16.81],
                    [100.26, 16.81],
                    [100.26, 16.82],
                    [100.25, 16.82],
                    [100.25, 16.81],
                ]
            ],
        },
        "crop_type": "rice",
        "province": "phitsanulok",
    }

    response = client.post("/api/v1/plots", json=payload)

    api_main.app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["plot_id"] == "PLT-007"
    query, params = fake_cursor.executed[0]
    assert "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)" in query
    assert json.loads(params[0]) == payload["geometry"]
    assert params[1:] == (7, 2, "PLT-007")
    assert fake_conn.committed is True


def test_get_hotspots_returns_geojson_collection():
    rows = [
        (16.82, 100.26, 320.5, "nominal", datetime(2026, 5, 17, 3, 15, 0), "VIIRS_SNPP"),
    ]
    fake_conn = FakeConnection(FakeCursor(rows=rows))

    def override_get_db():
        yield fake_conn

    api_main.app.dependency_overrides[api_main.get_db] = override_get_db
    client = TestClient(api_main.app)

    response = client.get("/api/v1/hotspots", params={"bbox": "100.2,16.8,100.3,16.9", "hours": 24})

    api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["geometry"]["coordinates"] == [100.26, 16.82]
    assert payload["features"][0]["properties"]["confidence"] == "medium"


def test_extract_stats_accepts_explicit_feature_vector_path(tmp_path, monkeypatch):
    processed_dir = tmp_path / "processed"
    indices_dir = processed_dir / "indices"
    indices_dir.mkdir(parents=True)

    feature_file = tmp_path / "feature_vector_test.json"
    feature_payload = {
        "plot_id": "PLT-001",
        "timestamp": "2026-05-19T06:00:00",
        "data_freshness_days": 2,
        "cloud_cover_pct": 12.5,
        "confidence": "high",
        "weather": {
            "rain_7d_mm": 45.2,
            "humidity_pct": 78.5,
            "wind_speed_kmh": 12.3,
            "wind_direction_deg": 225.0,
        },
        "fire": {
            "hotspot_count_24h": 0,
            "hotspot_count_7d": 2,
            "nearest_hotspot_km": 4.5,
        },
        "spi": {
            "spi_30d": -0.45,
        },
    }
    feature_file.write_text(json.dumps(feature_payload), encoding="utf-8")

    geojson_str = json.dumps({
        "type": "Polygon",
        "coordinates": [[[100.25, 16.81], [100.27, 16.81], [100.27, 16.83], [100.25, 16.83], [100.25, 16.81]]]
    })
    fake_cursor_geom = FakeCursor(row=(geojson_str,))   # cursor 0: geometry SELECT
    fake_cursor_insert = FakeCursor(row=(99,))           # cursor 1: INSERT RETURNING id
    fake_cursor_weather = FakeCursor()                   # cursor 2: ingest_weather_timeseries
    fake_conn = FakeConnection(fake_cursor_geom, fake_cursor_insert, fake_cursor_weather)

    monkeypatch.setattr(extract_stats_module, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(extract_stats_module.psycopg2, "connect", lambda _: fake_conn)

    result = extract_stats_module.main(str(feature_file))

    assert result == 99
    query, params = fake_cursor_insert.executed[0]
    assert "INSERT INTO plot_features" in query
    assert params[0] == 1        # plot_id
    assert params[8] == 45.2     # rain_7d_mm
    assert params[14] == 4.5     # nearest_hotspot_km
    assert params[15] == -0.45   # spi_30d
    updated_payload = json.loads(feature_file.read_text(encoding="utf-8"))
    assert updated_payload["indices"] == {"ndvi": 0.0, "ndmi": 0.0, "nbr": 0.0}


# =====================================================================
# Integrated Heaven's Eye BE Endpoints Unit Tests
# =====================================================================
from unittest.mock import MagicMock
from app.core.config import get_db as get_sqlalchemy_db
from app.models.domain import User, Plot

def test_health_check_returns_ok():
    client = TestClient(api_main.app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Heaven Eye Backend is running on FastAPI"}

def test_auth_register_creates_user():
    from datetime import datetime
    mock_db = MagicMock()
    # Mock that email doesn't exist yet
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    # Mock db.refresh to populate fields on new user
    def mock_refresh(user_obj):
        user_obj.id = 1
        user_obj.role = "farmer"
        user_obj.subscription_tier = "free"
        user_obj.created_at = datetime.utcnow()
        
    mock_db.refresh.side_effect = mock_refresh
    
    def override_sqlalchemy_db():
        yield mock_db
        
    api_main.app.dependency_overrides[get_sqlalchemy_db] = override_sqlalchemy_db
    client = TestClient(api_main.app)
    
    payload = {
        "name": "Test User",
        "email": "test@example.com",
        "password": "securepassword123"
    }
    response = client.post("/api/v1/auth/register", json=payload)
    api_main.app.dependency_overrides.clear()
    
    assert response.status_code == 201
    resp_data = response.json()
    assert resp_data["name"] == "Test User"
    assert resp_data["email"] == "test@example.com"
    assert "id" in resp_data

def test_auth_login_returns_jwt():
    from app.core.security import get_password_hash
    mock_db = MagicMock()
    fake_user = User(
        id=1,
        name="Test User",
        email="test@example.com",
        hashed_password=get_password_hash("securepassword123"),
        role="farmer",
        subscription_tier="free"
    )
    mock_db.query.return_value.filter.return_value.first.return_value = fake_user
    
    def override_sqlalchemy_db():
        yield mock_db
        
    api_main.app.dependency_overrides[get_sqlalchemy_db] = override_sqlalchemy_db
    client = TestClient(api_main.app)
    
    response = client.post("/api/v1/auth/login", data={"username": "test@example.com", "password": "securepassword123"})
    api_main.app.dependency_overrides.clear()
    
    assert response.status_code == 200
    resp_data = response.json()
    assert "access_token" in resp_data
    assert resp_data["token_type"] == "bearer"

def test_send_notification_mocked():
    mock_db = MagicMock()
    fake_user = User(
        id=1,
        name="Test User",
        email="test@example.com",
        hashed_password="hashedpassword",
        role="farmer",
        subscription_tier="free"
    )
    mock_db.query.return_value.filter.return_value.first.return_value = fake_user
    
    def override_sqlalchemy_db():
        yield mock_db
        
    api_main.app.dependency_overrides[get_sqlalchemy_db] = override_sqlalchemy_db
    client = TestClient(api_main.app)
    
    # Generate token
    from app.core.security import create_access_token
    token = create_access_token({"sub": "test@example.com"})
    
    payload = {
        "message": "⚠️ Fire warning near your plot!",
        "target_users": [1]
    }
    
    response = client.post(
        "/api/v1/notifications/send",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    api_main.app.dependency_overrides.clear()
    
    assert response.status_code == 202
    assert response.json()["status"] == "success"
    assert "queued" in response.json()["message"]

def test_create_plot_sqlalchemy():
    mock_db = MagicMock()
    fake_user = User(
        id=1,
        name="Test User",
        email="test@example.com",
        role="farmer"
    )
    mock_db.query.return_value.filter.return_value.first.return_value = fake_user
    
    # Mocking executing spatial SQL
    mock_db.execute.return_value.scalar.return_value = 1500.0
    
    # Mocking the returning row query
    mock_result = MagicMock()
    mock_result.id = 101
    mock_result.user_id = 1
    mock_result.plot_name = "Rice Field 1"
    mock_result.area_size = 1500.0
    mock_result.image_url = None
    mock_result.crop = None
    mock_result.address = None
    mock_result.geojson = '{"type": "Polygon", "coordinates": [[[100.5, 13.7], [100.6, 13.7], [100.6, 13.8], [100.5, 13.8], [100.5, 13.7]]]}'

    mock_db.query.return_value.filter.return_value.first.return_value = mock_result

    def override_sqlalchemy_db():
        yield mock_db
        
    api_main.app.dependency_overrides[get_sqlalchemy_db] = override_sqlalchemy_db
    client = TestClient(api_main.app)
    
    from app.core.security import create_access_token
    token = create_access_token({"sub": "test@example.com"})
    
    payload = {
        "plot_name": "Rice Field 1",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[[100.5, 13.7], [100.6, 13.7], [100.6, 13.8], [100.5, 13.8], [100.5, 13.7]]]
        }
    }
    
    response = client.post(
        "/api/v1/plots/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    api_main.app.dependency_overrides.clear()
    
    assert response.status_code == 201
    resp_data = response.json()
    assert resp_data["id"] == 101
    assert resp_data["plot_name"] == "Rice Field 1"
    assert resp_data["area_size"] == 1500.0


def test_upload_profile_image_success():
    import io
    mock_db = MagicMock()
    fake_user = User(
        id=42,
        name="Test User Profile",
        email="profile_test@example.com",
        role="farmer",
        subscription_tier="free",
        profile_image_url=None,
        created_at=datetime.utcnow()
    )
    mock_db.query.return_value.filter.return_value.first.return_value = fake_user

    def override_sqlalchemy_db():
        yield mock_db
        
    api_main.app.dependency_overrides[get_sqlalchemy_db] = override_sqlalchemy_db
    client = TestClient(api_main.app)
    
    from app.core.security import create_access_token
    token = create_access_token({"sub": "profile_test@example.com"})
    
    file_content = b"fake-image-bytes"
    file = io.BytesIO(file_content)
    
    response = client.post(
        "/api/v1/auth/me/upload-image",
        files={"file": ("profile.jpg", file, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"}
    )
    api_main.app.dependency_overrides.clear()
    
    assert response.status_code == 200
    resp_data = response.json()
    assert resp_data["id"] == 42
    assert resp_data["name"] == "Test User Profile"
    assert resp_data["email"] == "profile_test@example.com"
    assert "profile_image_url" in resp_data
    # Without SUPABASE_KEY storage falls back to an inline base64 data URI
    assert "data:image/jpeg;base64," in resp_data["profile_image_url"]


