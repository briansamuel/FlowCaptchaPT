"""Test script for FlowCaptchaPT API."""
import requests
import time

BASE_URL = "http://127.0.0.1:8899"
API_KEY = "fc_0OJCef-d--2iV-DVVSB3lavpJ5PIgozpRfJiK9WCqtk"

headers = {"X-Api-Key": API_KEY}


def test_video_token():
    print("=== Test VIDEO_GENERATION ===")
    start = time.time()
    resp = requests.post(
        f"{BASE_URL}/api/captcha",
        json={"action": "VIDEO_GENERATION"},
        headers=headers,
    )
    elapsed = round(time.time() - start, 2)
    data = resp.json()
    print(f"Status: {resp.status_code}")
    print(f"Success: {data.get('success')}")
    print(f"Token: {data.get('token', 'N/A')[:50]}..." if data.get("token") else f"Error: {data.get('error')}")
    if data.get("callback_url"):
        print(f"Callback URL: {data['callback_url']}")
    if data.get("job_id"):
        print(f"Job ID: {data['job_id']} (queued)")
    print(f"Time: {elapsed}s\n")
    return data


def test_image_token():
    print("=== Test IMAGE_GENERATION ===")
    start = time.time()
    resp = requests.post(
        f"{BASE_URL}/api/captcha",
        json={"action": "IMAGE_GENERATION"},
        headers=headers,
    )
    elapsed = round(time.time() - start, 2)
    data = resp.json()
    print(f"Status: {resp.status_code}")
    print(f"Success: {data.get('success')}")
    print(f"Token: {data.get('token', 'N/A')[:50]}..." if data.get("token") else f"Error: {data.get('error')}")
    print(f"Time: {elapsed}s\n")
    return data


def test_callback(callback_url):
    if not callback_url:
        print("No callback URL, skipping\n")
        return
    print("=== Test Callback ===")
    resp = requests.post(
        callback_url,
        json={"result": "success"},
        headers=headers,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}\n")


if __name__ == "__main__":
    # Test video token
    video = test_video_token()

    # Test image token
    image = test_image_token()

    # Test callback if we got one
    test_callback(video.get("callback_url"))

    print("Done.")
