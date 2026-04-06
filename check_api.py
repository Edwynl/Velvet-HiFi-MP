import httpx
import json

url = "http://127.0.0.1:8765/api/artists?limit=20"
try:
    r = httpx.get(url)
    print(f"Status: {r.status_code}")
    artists = r.json()
    for a in artists:
        print(f"Name: {a.get('name')}, Album Count: {a.get('album_count')}")
except Exception as e:
    print(f"Error: {e}")
