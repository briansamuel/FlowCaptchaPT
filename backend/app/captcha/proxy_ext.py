"""Generate a Chrome extension for proxy authentication."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def parse_proxy_url(proxy_url: str) -> dict:
    """Parse proxy URL into components.
    Supports: http://user:pass@host:port, https://user:pass@host:port
    """
    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"
    parsed = urlparse(proxy_url)
    return {
        "scheme": parsed.scheme or "http",
        "host": parsed.hostname or "",
        "port": parsed.port or 3128,
        "username": parsed.username or "",
        "password": parsed.password or "",
    }


def create_proxy_extension(proxy_url: str, ext_dir: str = None) -> str:
    """Create a Chrome extension directory for proxy auth. Returns path."""
    proxy = parse_proxy_url(proxy_url)

    if not ext_dir:
        ext_dir = os.path.join(tempfile.gettempdir(), "chrome_proxy_ext")

    os.makedirs(ext_dir, exist_ok=True)

    manifest = {
        "version": "1.0.0",
        "manifest_version": 3,
        "name": "Proxy Auth",
        "permissions": ["proxy", "webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"},
    }

    background_js = f"""
const config = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{
            scheme: "{proxy['scheme']}",
            host: "{proxy['host']}",
            port: {proxy['port']}
        }},
        bypassList: ["localhost", "127.0.0.1"]
    }}
}};

chrome.proxy.settings.set({{value: config, scope: "regular"}});

chrome.webRequest.onAuthRequired.addListener(
    function(details, callbackFn) {{
        callbackFn({{
            authCredentials: {{
                username: "{proxy['username']}",
                password: "{proxy['password']}"
            }}
        }});
    }},
    {{urls: ["<all_urls>"]}},
    ["asyncBlocking"]
);
"""

    with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    with open(os.path.join(ext_dir, "background.js"), "w") as f:
        f.write(background_js)

    return ext_dir
