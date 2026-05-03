"""Quick test: get captcha token and print full response."""
import requests
import time

BASE = "http://127.0.0.1:8899"
KEY = "fc_0OJCef-d--2iV-DVVSB3lavpJ5PIgozpRfJiK9WCqtk"

print("=== POST /api/captcha (IMAGE_GENERATION) ===")
start = time.time()
resp = requests.post(
    f"{BASE}/api/captcha",
    json={"action": "IMAGE_GENERATION"},
    headers={"X-Api-Key": KEY},
    timeout=120,
)
elapsed = round(time.time() - start, 2)
print(f"Status: {resp.status_code}")
print(f"Time: {elapsed}s")
print(f"Response: {resp.json()}")
