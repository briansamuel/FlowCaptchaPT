import requests

url = "http://127.0.0.1:8899/api/captcha/import-cookies"
headers = {"X-Api-Key": "fc_0OJCef-d--2iV-DVVSB3lavpJ5PIgozpRfJiK9WCqtk", "Content-Type": "application/json"}
data = {
    "url": "https://labs.google",
    "cookies": [
        {"domain": ".labs.google", "name": "_ga", "value": "GA1.1.699665278.1767286565", "path": "/"},
        {"domain": "labs.google", "name": "EMAIL", "value": "%22test%40test.com%22", "path": "/"},
        {"domain": "labs.google", "name": "__Secure-next-auth.session-token", "value": "test123", "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax"},
    ]
}

resp = requests.post(url, json=data, headers=headers, timeout=120)
print("Status:", resp.status_code)
print("Body:", resp.text)
