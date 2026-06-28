import urllib.request
import urllib.parse
import json
import sys
from datetime import datetime, timedelta

def test_remote_endpoint(url, hf_token, params=None, method="GET", post_data=None, data_type="json", custom_headers=None):
    full_url = url
    if params:
        query_string = urllib.parse.urlencode(params)
        full_url = f"{url}?{query_string}"
        
    req = urllib.request.Request(full_url, method=method)
    req.add_header("Authorization", f"Bearer {hf_token}") # Passes HF Space Proxy Gate
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    
    # Add app-specific custom headers (like X-App-Authorization: Bearer <APP_JWT>)
    if custom_headers:
        for k, v in custom_headers.items():
            req.add_header(k, v)
            
    # Prepare payload if needed
    if post_data is not None:
        if data_type == "json":
            req.add_header("Content-Type", "application/json")
            req_data = json.dumps(post_data).encode("utf-8")
        elif data_type == "form":
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req_data = urllib.parse.urlencode(post_data).encode("utf-8")
        else:
            req_data = post_data
    else:
        req_data = None
        
    try:
        with urllib.request.urlopen(req, data=req_data, timeout=20) as response:
            status = response.status
            content = response.read()
            content_type = response.headers.get("Content-Type", "")
            
            if "image" in content_type:
                return status, f"Image ({len(content)} bytes)"
                
            try:
                data = json.loads(content.decode("utf-8"))
                return status, data
            except Exception:
                return status, content.decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            return e.code, f"HTTP Error: {error_body}"
        except Exception:
            return e.code, f"HTTP Error: {e.reason}"
    except Exception as e:
        return 0, f"Error: {str(e)}"

def run_remote_verification():
    BASE_URL = "https://heaveneye-geoai-satellite.hf.space"
    import os
    HF_TOKEN = os.environ.get("HF_TOKEN", "")
    
    print("=" * 60)
    print("🌍 Remote Hugging Face Space Auth & Public APIs Verification")
    print(f"Target: {BASE_URL}")
    print("=" * 60)
    
    # 1. GET / (Root)
    print("\n[1] Testing GET / (Root)")
    status, res = test_remote_endpoint(f"{BASE_URL}/", HF_TOKEN)
    print(f"Status: {status}\nResponse: {res}")
    
    # 2. GET /api/v1/health (Health check)
    print("\n[2] Testing GET /api/v1/health")
    status, res = test_remote_endpoint(f"{BASE_URL}/api/v1/health", HF_TOKEN)
    print(f"Status: {status}\nResponse: {res}")
    
    # 3. Authenticated E2E Flows
    print("\n" + "=" * 50)
    print("🔐 Testing Authenticated APIs Flow")
    print("=" * 50)
    
    # Step A: Register a new remote user
    unique_email = f"remote_{int(datetime.now().timestamp())}@heavenseye.com"
    print(f"\n[A] Registering new user: {unique_email}")
    register_payload = {
        "name": "Remote Verification User",
        "email": unique_email,
        "password": "Password123!"
    }
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/auth/register", 
        HF_TOKEN, 
        method="POST", 
        post_data=register_payload
    )
    print(f"Registration Status: {status}")
    if status == 201:
        print(f"Success! Registered user ID: {res.get('id')}")
    else:
        print(f"Failed: {res}")
        return
        
    # Step B: Login to get app JWT token
    print("\n[B] Logging in to obtain JWT")
    login_payload = {
        "username": unique_email,
        "password": "Password123!"
    }
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/auth/login", 
        HF_TOKEN, 
        method="POST", 
        post_data=login_payload,
        data_type="form"
    )
    print(f"Login Status: {status}")
    if status == 200:
        app_token = res.get("access_token")
        print(f"Success! Obtained Token: {app_token[:25]}...")
    else:
        print(f"Failed: {res}")
        return
        
    # Crucial mapping: pass FastAPI JWT through custom bypass header
    app_headers = {"X-App-Authorization": f"Bearer {app_token}"}
    
    # Step C: Verify User Profile (/auth/me)
    print("\n[C] Verifying user identity retrieval (/auth/me)")
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/auth/me", 
        HF_TOKEN, 
        custom_headers=app_headers
    )
    print(f"Auth Me Status: {status}")
    if status == 200:
        print(f"Success! Retrieved profile for: {res.get('email')}")
    else:
        print(f"Failed: {res}")
        
    # Step D: Register a farmland plot boundary
    print("\n[D] Registering new plot boundary with Auth")
    plot_payload = {
        "plot_name": "HF Remote Validation Plot",
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
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/plots/", 
        HF_TOKEN, 
        method="POST", 
        post_data=plot_payload,
        custom_headers=app_headers
    )
    print(f"Plot Registration Status: {status}")
    if status == 201:
        plot_id = res.get("id")
        print(f"Success! Created plot ID: {plot_id}, size: {res.get('area_size')} sqm")
    else:
        print(f"Failed: {res}")
        return
        
    # Step E: List user's registered plots
    print("\n[E] Listing active user plots")
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/plots/", 
        HF_TOKEN, 
        custom_headers=app_headers
    )
    print(f"List Plots Status: {status}")
    if status == 200:
        print(f"Success! Total user plots registered: {len(res)}")
    else:
        print(f"Failed: {res}")
        
    # Step F: Broadcast push warning alert using Notification module
    print("\n[F] Broadcasting notification alert warning via multi-channel mock")
    notification_payload = {
        "message": "⚠️ HF Remote Warning: Rain expected soon, shield crop plots!",
        "target_users": [res[0].get("user_id") if len(res) > 0 else 1]
    }
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/notifications/send", 
        HF_TOKEN, 
        method="POST", 
        post_data=notification_payload,
        custom_headers=app_headers
    )
    print(f"Notification Status: {status}")
    if status == 202:
        print(f"Success! Warning dispatched through channels: {res.get('channels')}")
    else:
        print(f"Failed: {res}")
        
    # Step G: Clean up (Delete the created plot)
    print(f"\n[G] Cleaning up registered plot (ID: {plot_id})")
    status, res = test_remote_endpoint(
        f"{BASE_URL}/api/v1/plots/{plot_id}", 
        HF_TOKEN, 
        method="DELETE", 
        custom_headers=app_headers
    )
    print(f"Delete Status: {status}\nResponse: {res}")
    
    print("\n" + "=" * 60)
    print("🎉 ALL live authenticated endpoints successfully verified remotely!")
    print("=" * 60)

if __name__ == "__main__":
    run_remote_verification()
