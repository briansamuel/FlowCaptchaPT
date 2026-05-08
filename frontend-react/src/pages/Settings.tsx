import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Proxy { host: string; port: number; user: string; password: string; type: string }
interface S {
  headless: boolean; max_concurrent: number; cooldown: number; cooldown_fail: number; wait_delay: number
  proxy_enabled: boolean; proxies: Proxy[]
}

const defaults: S = {
  headless: false, max_concurrent: 3, cooldown: 0, cooldown_fail: 120, wait_delay: 3,
  proxy_enabled: false, proxies: [],
}

const emptyProxy: Proxy = { host: '', port: 0, user: '', password: '', type: 'socks5' }

export default function Settings() {
  const [s, setS] = useState<S>(defaults)
  const [saved, setSaved] = useState(false)
  const [bulkText, setBulkText] = useState('')

  useEffect(() => { api<S>('/api/settings').then(setS).catch(() => {}) }, [])

  async function save() {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(s) })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  function addProxy() {
    setS({ ...s, proxies: [...s.proxies, { ...emptyProxy }] })
  }

  function removeProxy(i: number) {
    setS({ ...s, proxies: s.proxies.filter((_, idx) => idx !== i) })
  }

  function updateProxy(i: number, field: keyof Proxy, value: string | number) {
    const list = [...s.proxies]
    list[i] = { ...list[i], [field]: value }
    setS({ ...s, proxies: list })
  }

  function parseBulk() {
    const lines = bulkText.trim().split('\n').filter(l => l.trim())
    const newProxies: Proxy[] = lines.map(line => {
      const parts = line.trim().split(':')
      return {
        host: parts[0] || '', port: parseInt(parts[1]) || 0,
        user: parts[2] || '', password: parts[3] || '', type: 'socks5',
      }
    })
    setS({ ...s, proxies: [...s.proxies, ...newProxies], proxy_enabled: true })
    setBulkText('')
  }

  const fields: { key: keyof S; label: string; desc: string; min?: number; max?: number }[] = [
    { key: 'max_concurrent', label: 'Max Concurrent', desc: 'Simultaneous browser extractions', min: 1, max: 10 },
    { key: 'cooldown', label: 'Cooldown (s)', desc: 'Delay after success (0 = none)', min: 0, max: 300 },
    { key: 'cooldown_fail', label: 'Fail Cooldown (s)', desc: 'Delay after failure', min: 0, max: 600 },
    { key: 'wait_delay', label: 'Wait Delay (s)', desc: 'Wait before extracting token', min: 0, max: 60 },
  ]

  return (
    <div>
      <h2 className="text-2xl font-bold text-white mb-1">Settings</h2>
      <p className="text-sm text-dark-300 mb-8">Runtime configuration</p>
      <div className="max-w-2xl space-y-5">
        {/* Headless toggle */}
        <div className="bg-dark-800 border border-dark-600 rounded-xl p-5 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-white">Headless Mode</p>
            <p className="text-xs text-dark-400 mt-1">Run Chrome without visible window</p>
          </div>
          <button onClick={() => setS({ ...s, headless: !s.headless })}
            className={`relative w-12 h-6 rounded-full transition-colors cursor-pointer ${s.headless ? 'bg-accent' : 'bg-dark-600'}`}>
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${s.headless ? 'translate-x-6' : ''}`} />
          </button>
        </div>

        {/* Number fields */}
        {fields.map(f => (
          <div key={f.key} className="bg-dark-800 border border-dark-600 rounded-xl p-5">
            <label className="block text-sm font-semibold text-white mb-1">{f.label}</label>
            <p className="text-xs text-dark-400 mb-3">{f.desc}</p>
            <input type="number" min={f.min} max={f.max}
              value={s[f.key] as number}
              onChange={e => setS({ ...s, [f.key]: +e.target.value })}
              className="w-full px-4 py-2.5 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent" />
          </div>
        ))}

        {/* Proxy Section */}
        <div className="bg-dark-800 border border-dark-600 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm font-semibold text-white">Proxy Pool (Rotate)</p>
              <p className="text-xs text-dark-400 mt-1">Route Flow API & Chrome through SOCKS5 proxies</p>
            </div>
            <button onClick={() => setS({ ...s, proxy_enabled: !s.proxy_enabled })}
              className={`relative w-12 h-6 rounded-full transition-colors cursor-pointer ${s.proxy_enabled ? 'bg-green-500' : 'bg-dark-600'}`}>
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${s.proxy_enabled ? 'translate-x-6' : ''}`} />
            </button>
          </div>

          {/* Bulk add */}
          <div className="mb-4">
            <label className="block text-xs text-dark-400 mb-2">Bulk add (one per line: host:port:user:pass)</label>
            <textarea value={bulkText} onChange={e => setBulkText(e.target.value)}
              rows={3} placeholder={"171.224.204.34:17787:user:pass\n10.0.0.1:1080:user2:pass2"}
              className="w-full px-3 py-2 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent font-mono" />
            <button onClick={parseBulk}
              className="mt-2 px-4 py-2 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm cursor-pointer">
              Add Proxies</button>
          </div>

          {/* Proxy list */}
          {s.proxies.length > 0 && (
            <div className="space-y-2 mb-4">
              <div className="grid grid-cols-12 gap-2 text-xs text-dark-400 px-1">
                <span className="col-span-1">#</span>
                <span className="col-span-3">Host</span>
                <span className="col-span-2">Port</span>
                <span className="col-span-2">User</span>
                <span className="col-span-3">Password</span>
                <span className="col-span-1"></span>
              </div>
              {s.proxies.map((p, i) => (
                <div key={i} className="grid grid-cols-12 gap-2 items-center">
                  <span className="col-span-1 text-xs text-dark-400 text-center">{i + 1}</span>
                  <input className="col-span-3 px-2 py-1.5 bg-dark-700 border border-dark-500 rounded text-white text-xs focus:outline-none focus:border-accent"
                    value={p.host} onChange={e => updateProxy(i, 'host', e.target.value)} placeholder="host" />
                  <input type="number" className="col-span-2 px-2 py-1.5 bg-dark-700 border border-dark-500 rounded text-white text-xs focus:outline-none focus:border-accent"
                    value={p.port} onChange={e => updateProxy(i, 'port', +e.target.value)} placeholder="port" />
                  <input className="col-span-2 px-2 py-1.5 bg-dark-700 border border-dark-500 rounded text-white text-xs focus:outline-none focus:border-accent"
                    value={p.user} onChange={e => updateProxy(i, 'user', e.target.value)} placeholder="user" />
                  <input type="password" className="col-span-3 px-2 py-1.5 bg-dark-700 border border-dark-500 rounded text-white text-xs focus:outline-none focus:border-accent"
                    value={p.password} onChange={e => updateProxy(i, 'password', e.target.value)} placeholder="pass" />
                  <button onClick={() => removeProxy(i)}
                    className="col-span-1 text-red-400 hover:text-red-300 text-sm cursor-pointer text-center">X</button>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button onClick={addProxy}
              className="px-4 py-2 bg-dark-600 hover:bg-dark-500 text-white rounded-lg text-sm cursor-pointer">
              + Add One</button>
            {s.proxies.length > 0 && (
              <button onClick={() => setS({ ...s, proxies: [] })}
                className="px-4 py-2 bg-red-900/50 hover:bg-red-800/50 text-red-300 rounded-lg text-sm cursor-pointer">
                Clear All</button>
            )}
          </div>

          {s.proxy_enabled && s.proxies.length > 0 && (
            <div className="mt-3 px-3 py-2 bg-green-900/30 border border-green-700/50 rounded-lg">
              <p className="text-xs text-green-400">
                Active: {s.proxies.length} proxy(s), round-robin rotate per request
              </p>
            </div>
          )}
        </div>

        <button onClick={save}
          className="w-full py-3 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm font-medium cursor-pointer">
          Save Settings</button>
        {saved && <p className="text-green-400 text-sm text-center">Saved</p>}
      </div>
    </div>
  )
}
