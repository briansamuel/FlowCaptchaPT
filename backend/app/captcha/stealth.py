"""
Stealth patches for Playwright to bypass reCAPTCHA Enterprise detection.
Injects scripts before any page JS runs to patch automation fingerprints.
"""

# All scripts run via addInitScript (before page JS)
STEALTH_SCRIPTS = [
    # 1. Patch navigator.webdriver
    """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });
    """,

    # 2. Patch chrome runtime (missing in headless)
    """
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            connect: function() {},
            sendMessage: function() {},
            onMessage: { addListener: function() {} },
            id: undefined,
        };
    }
    """,

    # 3. Patch permissions API (headless returns inconsistent results)
    """
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    """,

    # 4. Patch plugins (headless has empty plugins)
    """
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer',
                  description: 'Portable Document Format',
                  length: 1, item: () => ({type: 'application/x-google-chrome-pdf'}) },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                  description: '', length: 1, item: () => ({}) },
                { name: 'Native Client', filename: 'internal-nacl-plugin',
                  description: '', length: 2, item: () => ({}) },
            ];
            plugins.length = 3;
            plugins.item = (i) => plugins[i] || null;
            plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
            plugins.refresh = () => {};
            plugins[Symbol.iterator] = function*() { for (const p of [plugins[0], plugins[1], plugins[2]]) yield p; };
            return plugins;
        },
        configurable: true,
    });
    """,

    # 5. Patch languages
    """
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true,
    });
    Object.defineProperty(navigator, 'language', {
        get: () => 'en-US',
        configurable: true,
    });
    """,

    # 6. Patch WebGL vendor/renderer (headless shows "Google SwiftShader")
    """
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Google Inc. (NVIDIA)';
        if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        return getParameter.call(this, parameter);
    };
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Google Inc. (NVIDIA)';
        if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        return getParameter2.call(this, parameter);
    };
    """,

    # 7. Patch platform & hardwareConcurrency
    """
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true,
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true,
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true,
    });
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0,
        configurable: true,
    });
    """,

    # 8. Remove Playwright/CDP traces from stacktrace
    """
    const originalError = Error;
    const originalCaptureStackTrace = Error.captureStackTrace;
    Error.captureStackTrace = function(obj, fn) {
        originalCaptureStackTrace.call(this, obj, fn);
        if (obj.stack) {
            obj.stack = obj.stack.replace(/\\n.*pptr.*\\n/g, '\\n')
                                .replace(/\\n.*playwright.*\\n/g, '\\n')
                                .replace(/\\n.*puppeteer.*\\n/g, '\\n');
        }
    };
    """,

    # 9. Patch iframe contentWindow access (detection via cross-origin)
    """
    const originalAttachShadow = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function() {
        return originalAttachShadow.call(this, ...arguments);
    };
    """,

    # 10. Patch connection rtt (headless returns 0)
    """
    if (navigator.connection) {
        Object.defineProperty(navigator.connection, 'rtt', {
            get: () => 50,
            configurable: true,
        });
    }
    """,
]


def get_stealth_script() -> str:
    """Combine all stealth patches into one script."""
    combined = "(() => {\n"
    for i, script in enumerate(STEALTH_SCRIPTS):
        combined += f"  // Patch {i+1}\n"
        combined += f"  try {{\n    {script.strip()}\n  }} catch(e) {{}}\n\n"
    combined += "})();"
    return combined


# Human-like behavior simulation
HUMAN_BEHAVIOR_SCRIPT = """
async () => {
    // Random mouse movements
    const moves = 3 + Math.floor(Math.random() * 5);
    for (let i = 0; i < moves; i++) {
        const x = 100 + Math.floor(Math.random() * 1000);
        const y = 100 + Math.floor(Math.random() * 500);
        window.dispatchEvent(new MouseEvent('mousemove', {
            clientX: x, clientY: y, bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50 + Math.random() * 200));
    }

    // Small scroll
    window.scrollBy(0, 50 + Math.floor(Math.random() * 100));
    await new Promise(r => setTimeout(r, 200 + Math.random() * 500));
    window.scrollBy(0, -(30 + Math.floor(Math.random() * 50)));
}
"""
