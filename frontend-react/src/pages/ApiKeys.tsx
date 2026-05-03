import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Key {
  id: string; name: string; key_prefix: string; is_active: boolean
  rate_limit: number; allowed_actions: string; total_requests: number; success_count: number
}

export default function ApiKeys() {
  const [keys, setKeys] = useState<Key[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [form, setForm] = useState({ name: '', rate_limit: 60, allowed_actions: 'ALL' })

  const load = () => api<Key[]>('/api/keys').then(setKeys).catch(() => {})
  useEffect(() => { load() }, [])

  async function create() {
    if (!form.name) return
    const res = await api<any>('/api/keys', { method: 'POST', body: JSON.stringify(form) })
    setNewKey(res.key)
    setShowCreate(false)
    setForm({ name: '', rate_limit: 60, allowed_actions: 'ALL' })
    load()
  }

  async function toggle(id: string) { await api(`/api/keys/${id}/toggle`, { method: 'PUT' }); load() }
  async function del(id: string, name: string) {
    if (!confirm(`Delete "${name}"?`)) return
    await api(`/api/keys/${id}`, { method: 'DELETE' }); load()
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white">API Keys</h2>
          <p className="text-sm text-dark-300 mt-1">Manage access</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="px-4 py-2.5 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm font-medium cursor-pointer">
          + Create Key</button>
      </div>

      {newKey && (
        <div className="bg-dark-800 border border-green-500/30 rounded-xl p-4 mb-6">
          <p className="text-sm text-yellow-400 mb-2">Copy this key now. It won't be shown again.</p>
          <div className="flex items-center gap-2 bg-dark-700 rounded-lg p-3">
            <code className="flex-1 text-xs text-accent-light break-all font-mono">{newKey}</code>
            <button onClick={() => { navigator.clipboard.writeText(newKey); }}
              className="px-3 py-1.5 bg-accent/20 text-accent-light rounded text-xs cursor-pointer">Copy</button>
          </div>
          <button onClick={() => setNewKey('')} className="mt-2 text-xs text-dark-400 cursor-pointer">Dismiss</button>
        </div>
      )}

      <div className="bg-dark-800 border border-dark-600 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-dark-600 text-dark-300">
              <th className="text-left px-5 py-3 font-medium">Name</th>
              <th className="text-left px-5 py-3 font-medium">Prefix</th>
              <th className="text-left px-5 py-3 font-medium">Actions</th>
              <th className="text-left px-5 py-3 font-medium">Rate</th>
              <th className="text-left px-5 py-3 font-medium">Usage</th>
              <th className="text-left px-5 py-3 font-medium">Status</th>
              <th className="text-right px-5 py-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {keys.map(k => (
              <tr key={k.id} className="border-b border-dark-700 hover:bg-dark-700/50">
                <td className="px-5 py-3 text-white font-medium">{k.name}</td>
                <td className="px-5 py-3 font-mono text-dark-300 text-xs">{k.key_prefix}...</td>
                <td className="px-5 py-3"><span className="px-2 py-1 rounded text-xs font-medium bg-accent/20 text-accent-light">{k.allowed_actions}</span></td>
                <td className="px-5 py-3 text-dark-200">{k.rate_limit}/min</td>
                <td className="px-5 py-3 text-dark-200">{k.total_requests} <span className="text-green-400 text-xs">({k.success_count} ok)</span></td>
                <td className="px-5 py-3"><span className={`px-2 py-1 rounded text-xs font-medium ${k.is_active ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>{k.is_active ? 'Active' : 'Disabled'}</span></td>
                <td className="px-5 py-3 text-right space-x-2">
                  <button onClick={() => toggle(k.id)} className="text-xs text-dark-300 hover:text-white cursor-pointer">{k.is_active ? 'Disable' : 'Enable'}</button>
                  <button onClick={() => del(k.id, k.name)} className="text-xs text-red-400 hover:text-red-300 cursor-pointer">Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!keys.length && <p className="px-5 py-12 text-center text-dark-400 text-sm">No API keys yet.</p>}
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center">
          <div className="bg-dark-800 border border-dark-600 rounded-2xl p-6 w-full max-w-md">
            <h3 className="text-lg font-bold text-white mb-4">Create API Key</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-dark-300 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                  className="w-full px-4 py-2.5 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent" />
              </div>
              <div>
                <label className="block text-sm text-dark-300 mb-1">Rate Limit (req/min)</label>
                <input type="number" value={form.rate_limit} onChange={e => setForm({ ...form, rate_limit: +e.target.value })}
                  className="w-full px-4 py-2.5 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent" />
              </div>
              <div>
                <label className="block text-sm text-dark-300 mb-1">Allowed Actions</label>
                <select value={form.allowed_actions} onChange={e => setForm({ ...form, allowed_actions: e.target.value })}
                  className="w-full px-4 py-2.5 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent">
                  <option value="ALL">ALL</option>
                  <option value="VIDEO">VIDEO only</option>
                  <option value="IMAGE">IMAGE only</option>
                </select>
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button onClick={() => setShowCreate(false)} className="flex-1 py-2.5 bg-dark-700 text-dark-200 rounded-lg text-sm cursor-pointer">Cancel</button>
              <button onClick={create} className="flex-1 py-2.5 bg-accent text-white rounded-lg text-sm font-medium cursor-pointer">Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
