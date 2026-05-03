/**
 * FlowCaptchaPT - Shared JS utilities
 */

const API_BASE = ''; // Same origin

function getToken() {
    return localStorage.getItem('fc_admin_token') || '';
}

function setToken(token) {
    localStorage.setItem('fc_admin_token', token);
}

async function api(path, options = {}) {
    const token = getToken();
    if (!token && !path.includes('/captcha')) {
        showLogin();
        throw new Error('Not authenticated');
    }

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(API_BASE + path, {
        ...options,
        headers
    });

    if (res.status === 401 || res.status === 403) {
        showLogin();
        throw new Error('Unauthorized');
    }
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

function showLogin() {
    const existing = document.getElementById('login-modal');
    if (existing) return;

    const modal = document.createElement('div');
    modal.id = 'login-modal';
    modal.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center';
    modal.innerHTML = `
        <div class="bg-dark-800 border border-dark-600 rounded-2xl p-8 w-full max-w-sm mx-4">
            <h2 class="text-xl font-bold text-white mb-2">Admin Login</h2>
            <p class="text-sm text-dark-300 mb-6">Enter your admin token</p>
            <input id="login-input" type="password" placeholder="Admin token"
                class="w-full px-4 py-3 bg-dark-700 border border-dark-500 rounded-lg text-white placeholder-dark-400 focus:outline-none focus:border-accent text-sm mb-4">
            <button id="login-btn" class="w-full py-3 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm font-medium transition-colors duration-200 cursor-pointer">
                Login
            </button>
            <p id="login-error" class="text-danger text-xs mt-3 hidden"></p>
        </div>
    `;
    document.body.appendChild(modal);

    const input = document.getElementById('login-input');
    const btn = document.getElementById('login-btn');

    async function doLogin() {
        const token = input.value.trim();
        if (!token) return;
        setToken(token);
        try {
            await api('/api/dashboard/stats');
            modal.remove();
            location.reload();
        } catch {
            const err = document.getElementById('login-error');
            err.textContent = 'Invalid token';
            err.classList.remove('hidden');
            localStorage.removeItem('fc_admin_token');
        }
    }

    btn.addEventListener('click', doLogin);
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });
    input.focus();
}

// Check auth on page load (skip for cookies page)
if (!getToken() && !location.pathname.includes('cookies')) showLogin();