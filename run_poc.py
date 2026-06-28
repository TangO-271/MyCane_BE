"""
🛰️ Satellite Team — PRODUCTION BATCH PIPELINE
รัน pipeline ทดสอบทั้งหมดในครั้งเดียว — Multi-Tile Sentinel-2 support
"""
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.config import DEMO_PLOT, RAW_DIR, PROCESSED_DIR, check_api_keys


def run_step(step_name: str, func, *args, **kwargs):
    """รัน step พร้อมจับ error"""
    print("\n" + "=" * 60)
    print(f"▶️  Step: {step_name}")
    print("=" * 60)

    try:
        result = func(*args, **kwargs)
        print(f"\n✅ {step_name} — SUCCESS")
        return result
    except Exception as e:
        print(f"\n❌ {step_name} — FAILED: {e}")
        import traceback
        traceback.print_exc()
        return None


# Known Sentinel-2 coverage areas for our 5 provinces
# Each province maps to a BBOX that covers all plots within it
PROVINCE_TILES = {
    "phitsanulok": {
        "bbox": [100.0, 16.5, 100.9, 17.2],
        "plots": [],
    },
    "uttaradit": {
        "bbox": [100.0, 17.3, 100.7, 18.2],
        "plots": [],
    },
    "phetchabun": {
        "bbox": [100.8, 15.8, 101.5, 16.8],
        "plots": [],
    },
    "nakhon_sawan": {
        "bbox": [99.8, 15.3, 100.6, 16.2],
        "plots": [],
    },
    "samut_songkhram": {
        "bbox": [99.5, 13.2, 100.5, 14.5],
        "plots": [],
    },
}


def group_plots_by_tile(plots):
    """
    จัดกลุ่มแปลงตาม 5 จังหวัดที่เรารู้อยู่แล้ว
    ถ้าแปลงไหนไม่อยู่ใน 5 จังหวัด จะ fallback ไปกลุ่ม dynamic
    """
    import copy
    tile_groups = copy.deepcopy(PROVINCE_TILES)
    
    unmatched = []
    for plot_row in plots:
        plot_id, plot_name, lat, lon = plot_row
        matched = False
        for prov_name, prov_info in tile_groups.items():
            bbox = prov_info["bbox"]
            if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
                prov_info["plots"].append(plot_row)
                matched = True
                break
        if not matched:
            unmatched.append(plot_row)
    
    # Fallback: dynamic 1° grid for plots outside 5 provinces
    for plot_row in unmatched:
        plot_id, plot_name, lat, lon = plot_row
        grid_lat = round(lat, 0)
        grid_lon = round(lon, 0)
        tile_key = f"dynamic_{grid_lat:.0f}_{grid_lon:.0f}"
        if tile_key not in tile_groups:
            tile_groups[tile_key] = {
                "bbox": [grid_lon - 0.6, grid_lat - 0.6, grid_lon + 0.6, grid_lat + 0.6],
                "plots": [],
            }
        tile_groups[tile_key]["plots"].append(plot_row)
    
    # Remove provinces with 0 plots
    return {k: v for k, v in tile_groups.items() if len(v["plots"]) > 0}


def precompute_sentinel2_tiles(tile_groups: dict, keys: dict):
    """
    Phase 1: Search, download, and compute indices for each unique tile.
    Downloads ALL unique Sentinel-2 tile grids from STAC results (not just the first).
    Returns a dict mapping tile_key -> scene_info for use in per-plot processing.
    """
    from pipeline.ingestion.fetch_sentinel2 import search_sentinel2_scenes, get_download_info
    from pipeline.ingestion.download_sentinel2 import download_band_from_stac, get_cdse_token
    from pipeline.config import COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    
    tile_scenes = {}
    
    for tile_key, tile_info in tile_groups.items():
        bbox = tile_info["bbox"]
        plot_count = len(tile_info["plots"])
        
        print(f"\n{'=' * 60}")
        print(f"🗺️  TILE: {tile_key} ({plot_count} plots) | BBOX: {bbox}")
        print(f"{'=' * 60}")
        
        # 1. Search
        results = search_sentinel2_scenes(
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
            max_cloud_cover=30,
            limit=5,
        )
        
        if not results:
            print(f"  ⚠️ No Sentinel-2 scenes found for tile {tile_key}")
            tile_scenes[tile_key] = None
            continue
        
        # Extract unique Sentinel-2 tile grids (e.g., T47QPV, T47QPU)
        # Pick the best (most recent) scene per tile grid
        seen_grids = {}
        for scene in results:
            sid = scene["id"]
            # Extract tile grid from scene ID: ...T47QPV... -> T47QPV
            parts = sid.split("_")
            grid = next((p for p in parts if len(p) == 6 and p[0] == "T" and p[1:3].isdigit()), None)
            if grid and grid not in seen_grids:
                seen_grids[grid] = scene
        
        print(f"  📦 Found {len(seen_grids)} unique tile grids: {', '.join(seen_grids.keys())}")
        
        # Save search results
        sentinel_dir = RAW_DIR / "sentinel2"
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        search_file = sentinel_dir / f"search_results_{tile_key}_{datetime.now().strftime('%Y%m%d')}.json"
        with open(search_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        
        has_keys = bool(COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET)
        best_scene = results[0]
        
        # 2. Download and compute indices for EACH unique tile grid
        for grid_id, scene in seen_grids.items():
            scene_id = scene["id"]
            scene_dir = sentinel_dir / scene_id
            scene_dir.mkdir(parents=True, exist_ok=True)
            
            bands_config = {
                "B04": "B04_10m",
                "B08": "B08_10m",
                "B11": "B11_20m",
                "B12": "B12_20m",
            }
            
            all_exist = True
            for band_name, stac_key in bands_config.items():
                out_path = scene_dir / f"{scene_id}_{band_name}.tif"
                if out_path.exists() and out_path.stat().st_size > 50000:
                    print(f"  ✅ {band_name} already exists ({out_path.stat().st_size / 1024:.0f} KB)")
                    continue
                if has_keys:
                    success = download_band_from_stac(scene, stac_key, out_path)
                    if not success:
                        print(f"  ⚠️ Failed to download {band_name} for {grid_id}")
                        all_exist = False
                else:
                    all_exist = False
            
            # 3. Compute NDVI
            from pipeline.index_computation.compute_ndvi import compute_ndvi
            red_file = scene_dir / f"{scene_id}_B04.tif"
            nir_file = scene_dir / f"{scene_id}_B08.tif"
            out_dir = PROCESSED_DIR / "indices" / scene_id
            ndvi_out = out_dir / f"{scene_id}_NDVI.tif"
            
            if red_file.exists() and nir_file.exists():
                if not ndvi_out.exists():
                    try:
                        compute_ndvi(str(red_file), str(nir_file), str(ndvi_out))
                        print(f"  ✅ NDVI computed for {grid_id}")
                    except Exception as e:
                        print(f"  ⚠️ NDVI failed for {grid_id}: {e}")
                else:
                    print(f"  ✅ NDVI already exists for {grid_id}")
            
            # 4. Compute NDMI & NBR
            from pipeline.index_computation.compute_ndmi_nbr import compute_index, ingest_burn_scars_to_db
            swir1_file = scene_dir / f"{scene_id}_B11.tif"
            swir2_file = scene_dir / f"{scene_id}_B12.tif"
            ndmi_out = out_dir / f"{scene_id}_NDMI.tif"
            nbr_out = out_dir / f"{scene_id}_NBR.tif"
            
            if nir_file.exists() and swir1_file.exists() and not ndmi_out.exists():
                try:
                    compute_index(nir_file, swir1_file, ndmi_out, "NDMI")
                    print(f"  ✅ NDMI computed for {grid_id}")
                except Exception as e:
                    print(f"  ⚠️ NDMI failed for {grid_id}: {e}")
            
            if nir_file.exists() and swir2_file.exists() and not nbr_out.exists():
                try:
                    compute_index(nir_file, swir2_file, nbr_out, "NBR")
                    print(f"  ✅ NBR computed for {grid_id}")
                    ingest_burn_scars_to_db(scene_id, nbr_out)
                except Exception as e:
                    print(f"  ⚠️ NBR failed for {grid_id}: {e}")
        
        # Store best scene info for metadata and all processed scenes
        tile_scenes[tile_key] = {
            "scene_id": best_scene["id"],
            "scene_dir": str(sentinel_dir / best_scene["id"]),
            "indices_dir": str(PROCESSED_DIR / "indices" / best_scene["id"]),
            "cloud_cover": best_scene.get("cloud_cover", 9.12),
            "datetime": best_scene.get("datetime"),
            "scenes": {
                scene["id"]: {
                    "cloud_cover": scene.get("cloud_cover", 9.12),
                    "datetime": scene.get("datetime")
                } for scene in seen_grids.values()
            }
        }
        
        print(f"  🎯 Tile {tile_key} → {len(seen_grids)} grids processed (best: {best_scene['id']})")
    
    return tile_scenes


def get_tile_key_for_plot(lat, lon):
    """หา tile_key สำหรับแปลง — ใช้ province name ที่ตรงกับ Phase 1"""
    for prov_name, prov_info in PROVINCE_TILES.items():
        bbox = prov_info["bbox"]
        if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
            return prov_name
    # Fallback: dynamic grid สำหรับแปลงนอก 5 จังหวัด
    grid_lat = round(lat, 0)
    grid_lon = round(lon, 0)
    return f"dynamic_{grid_lat:.0f}_{grid_lon:.0f}"


def main(phase1=True, phase2=True):
    import pipeline.config
    import psycopg2
    from psycopg2 import sql
    
    print("🛰️" * 30)
    print(f"   SATELLITE TEAM — PRODUCTION BATCH PIPELINE (Phase 1={phase1}, Phase 2={phase2})")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("🛰️" * 30)

    # Step 0: Check API Keys
    keys = check_api_keys()

    # Step 0.5: Fetch all plots from DB
    print("\n🔍 Fetching all registered plots from the database...")
    try:
        conn = psycopg2.connect(pipeline.config.DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, plot_name, ST_Y(ST_Centroid(ST_Transform(geometry, 4326))) AS lat, ST_X(ST_Centroid(ST_Transform(geometry, 4326))) AS lon 
                FROM plots 
                ORDER BY id;
            """)
            plots_in_db = cur.fetchall()
    except Exception as e:
        print(f"❌ Failed to connect to DB: {e}")
        return
    finally:
        if 'conn' in locals() and conn:
            conn.close()

    if not plots_in_db:
        print("⚠️ No plots found in database. Exiting pipeline.")
        return

    print(f"✅ Found {len(plots_in_db)} plots to process.")

    cache_file = pipeline.config.PROCESSED_DIR / "tile_scenes_cache.json"
    tile_scenes = {}

    # ============================================
    # PHASE 1: Pre-compute Sentinel-2 for all tiles
    # ============================================
    if phase1:
        print("\n" + "=" * 60)
        print("🗺️  PHASE 1: Multi-Tile Sentinel-2 Pre-computation")
        print("=" * 60)
        
        tile_groups = group_plots_by_tile(plots_in_db)
        print(f"📊 Grouped {len(plots_in_db)} plots into {len(tile_groups)} tiles:")
        for tk, tv in tile_groups.items():
            print(f"   Tile {tk}: {len(tv['plots'])} plots, BBOX={tv['bbox']}")
        
        tile_scenes = precompute_sentinel2_tiles(tile_groups, keys)
        
        tiles_with_data = sum(1 for v in tile_scenes.values() if v is not None)
        print(f"\n✅ Phase 1 complete: {tiles_with_data}/{len(tile_groups)} tiles with data")

        # Save to cache file
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(tile_scenes, f, indent=2, ensure_ascii=False)
            print(f"💾 Saved tile_scenes cache to {cache_file}")
        except Exception as e:
            print(f"⚠️ Failed to save tile_scenes cache: {e}")
    else:
        # Load from cache file
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    tile_scenes = json.load(f)
                print(f"💾 Loaded tile_scenes cache from {cache_file}")
            except Exception as e:
                print(f"⚠️ Failed to load tile_scenes cache: {e}")
                tile_scenes = {}
        else:
            print(f"⚠️ Cache file {cache_file} not found. Running Phase 1 to build it...")
            tile_groups = group_plots_by_tile(plots_in_db)
            tile_scenes = precompute_sentinel2_tiles(tile_groups, keys)
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(tile_scenes, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"⚠️ Failed to save tile_scenes cache: {e}")

    # ============================================
    # PHASE 2: Per-plot processing
    # ============================================
    output_dir = PROCESSED_DIR / "poc"
    if phase2:
        from pipeline.ingestion.fetch_weather import main as fetch_openmeteo_weather
        from pipeline.ingestion.fetch_tmd_weather import main as fetch_tmd_weather
        from pipeline.ingestion.fetch_burn_scars import main as fetch_fire_features
        from pipeline.zonal_stats.extract_stats import main as extract_stats

        from concurrent.futures import ThreadPoolExecutor
        import threading

        success_count = 0
        success_lock = threading.Lock()

        def process_single_plot(plot_row):
            plot_id, plot_name, lat, lon = plot_row
            province = "phitsanulok"  # Default fallback since DB lacks province column
            print(f"\n🚀 [Thread] PROCESSING PLOT: {plot_name} (ID: {plot_id}) | Lat: {lat:.4f}, Lon: {lon:.4f}")

            # 💡 DYNAMICALLY OVERRIDE the global DEMO_PLOT config (thread-safe due to ThreadLocalDict)
            pipeline.config.DEMO_PLOT.update({
                "plot_id": str(plot_id),
                "plot_name": plot_name or f"Plot {plot_id}",
                "center": {
                    "lat": float(lat),
                    "lon": float(lon)
                },
                "province": province or "phitsanulok",
                "crop_type": "rice"
            })

            try:
                # Step 1: Open-Meteo Weather
                openmeteo_features = run_step(f"Weather Data (Open-Meteo) for {plot_name}", fetch_openmeteo_weather)

                # Step 2: TMD Weather
                tmd_features = None
                if keys.get("TMD"):
                    tmd_features = run_step(f"Weather Data (TMD API) for {plot_name}", fetch_tmd_weather)

                # Step 3: Fire & Hotspots (NASA FIRMS)
                fire_features = run_step(f"Fire & Hotspot Features (NASA FIRMS) for {plot_name}", fetch_fire_features)

                # Look up Sentinel-2 data from Phase 1
                tile_key = get_tile_key_for_plot(lat, lon)
                scene_info = tile_scenes.get(tile_key)
                
                # Compute per-plot confidence from actual tile scene data
                if scene_info:
                    cloud_cover_pct = scene_info.get("cloud_cover", 9.12)
                    if isinstance(cloud_cover_pct, str) or cloud_cover_pct is None:
                        cloud_cover_pct = 50.0
                    s2_date_str = scene_info.get("datetime")
                    try:
                        s2_dt = datetime.fromisoformat(str(s2_date_str).replace("Z", "+00:00"))
                        data_freshness_days = (datetime.now(s2_dt.tzinfo) - s2_dt).days
                    except Exception:
                        data_freshness_days = 30
                else:
                    cloud_cover_pct = 100.0  # No scene available
                    data_freshness_days = 999

                if data_freshness_days <= 3 and cloud_cover_pct < 20.0:
                    confidence_level = "high"
                elif data_freshness_days <= 7 and cloud_cover_pct < 50.0:
                    confidence_level = "medium"
                else:
                    confidence_level = "low"

                # Combine weather features (only the fields stored in plot_features)
                weather_combined = {}
                spi_data = {}
                if openmeteo_features:
                    weather_combined["wind_speed_kmh"] = openmeteo_features.get("wind_speed_kmh", 0.0)
                    weather_combined["wind_direction_deg"] = openmeteo_features.get("wind_direction_deg", 0.0)
                    spi_data = {"spi_30d": openmeteo_features.get("spi_30d", 0.0)}
                    weather_combined["rain_7d_mm"] = openmeteo_features.get("rain_7d_mm", 0.0)
                    weather_combined["humidity_pct"] = openmeteo_features.get("humidity_pct", 70.0)

                if tmd_features:
                    weather_combined["rain_7d_mm"] = tmd_features.get("rain_7d_mm", weather_combined.get("rain_7d_mm", 0.0))
                    weather_combined["humidity_pct"] = tmd_features.get("humidity_pct", weather_combined.get("humidity_pct", 70.0))

                fire_data = {
                    "hotspot_count_24h": 0, "hotspot_count_7d": 0,
                    "nearest_hotspot_km": 999.0,
                }
                if fire_features:
                    fire_data.update({
                        k: v for k, v in fire_features.items()
                        if k in ("hotspot_count_24h", "hotspot_count_7d", "nearest_hotspot_km")
                    })

                feature_vector = {
                    "plot_id": pipeline.config.DEMO_PLOT["plot_id"],
                    "timestamp": datetime.now().isoformat(),
                    "data_freshness_days": data_freshness_days,
                    "cloud_cover_pct": cloud_cover_pct,
                    "confidence": confidence_level,
                    "indices": {"ndvi": 0.0, "ndmi": 0.0, "nbr": 0.0},
                    "weather": weather_combined,
                    "fire": fire_data,
                    "spi": spi_data,
                }

                output_dir.mkdir(parents=True, exist_ok=True)
                output_file = output_dir / f"feature_vector_{pipeline.config.DEMO_PLOT['plot_name']}_{datetime.now().strftime('%Y%m%d%H')}.json"

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(feature_vector, f, indent=2, ensure_ascii=False, default=str)

                # Step 9: Zonal Stats & Database Ingestion
                db_result = run_step(f"Zonal Stats & DB Ingestion for {plot_name}", extract_stats, str(output_file))

                if db_result:
                    print(f"✅ Successfully processed plot {plot_id}.")
                    with success_lock:
                        nonlocal success_count
                        success_count += 1
                else:
                    print(f"⚠️ Processed plot {plot_id} but DB ingestion failed.")

            except Exception as e:
                print(f"❌ CRITICAL ERROR on plot {plot_id}: {e}")

        # Run concurrently with ThreadPoolExecutor (max_workers=10 for high concurrent throughput)
        print(f"🚀 Starting parallel ingestion of {len(plots_in_db)} plots using 10 threads...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(process_single_plot, plots_in_db)

        print("\n" + "=" * 60)
        print(f"✅ BATCH PIPELINE COMPLETE. Successfully processed {success_count}/{len(plots_in_db)} plots.")
        print("=" * 60)

    # Step 10: Upload to S3
    print("\n☁️  Step 10: Upload processed files to S3")
    try:
        from pipeline.utils.s3_client import upload_to_s3
        if phase2:
            for json_file in output_dir.glob("*.json"):
                s3_key = f"processed/poc/{json_file.name}"
                upload_to_s3(str(json_file), s3_key)
            
        if phase1:
            indices_dir = PROCESSED_DIR / "indices"
            if indices_dir.exists():
                for tif_file in indices_dir.glob("**/*.tif"):
                    s3_key = f"processed/indices/{tif_file.parent.name}/{tif_file.name}"
                    upload_to_s3(str(tif_file), s3_key)
        print("✅ S3 Upload Complete.")
    except Exception as e:
        print(f"⚠️ S3 Upload Skipped or Failed: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Satellite Team Batch Ingestion Pipeline")
    parser.add_argument("--no-phase1", action="store_true", help="Skip Phase 1 (Sentinel-2 tile processing)")
    parser.add_argument("--no-phase2", action="store_true", help="Skip Phase 2 (Per-plot features)")
    args = parser.parse_args()
    
    main(phase1=not args.no_phase1, phase2=not args.no_phase2)
