import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface S { headless: boolean; max_concurrent: number; cooldown: number; cooldown_fail: number; wait_delay: number }

export default function Settings() {
  const [s, setS] = useState<S>({ headless: false, max_concurrent: 3, cooldown: 0, cooldown_fail: 120, wait_delay: 3 })
  const [saved, setSaved] = useState(false)

  useEffect(() => { api<S>('/api/settings').then(setS).catch(() => {}) }, [])

  async function save() {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(s) })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const fields: { key: keyof S; label: string; desc: string; type: string; min?: number; max?: number }[] = [
    { key: 'max_concurrent', label: 'Max Concurrent', desc: 'Simultaneous browser extractions', type: 'number', min: 1, max: 10 },
    { key: 'cooldown', label: 'Cooldown (s)', desc: 'Delay after success (0 = none)', type: 'number', min: 0, max: 300 },
    { key: 'cooldown_fail', label: 'Fail Cooldown (s)', desc: 'Delay after failure', type: 'number', min: 0, max: 600 },
    { key: 'wait_delay', label: 'Wait Delay (s)', desc: 'Wait before extracting token', type: 'number', min: 0, max: 60 },
  ]

  return (
    <div>
      <h2 className="text-2xl font-bold text-white mb-1">Settings</h2>
      <p className="text-sm text-dark-300 mb-8">Runtime configuration</p>
      <div className="max-w-xl space-y-5">
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

        <button onClick={save}
          className="w-full py-3 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm font-medium cursor-pointer">
          Save Settings</button>
        {saved && <p className="text-green-400 text-sm text-center">Saved</p>}
      </div>
    </div>
  )
}
