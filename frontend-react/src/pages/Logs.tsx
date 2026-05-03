import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Log {
  id: string; action: string; success: boolean; error: string | null
  token_preview: string | null; ip_address: string | null
  response_time_ms: number | null; callback_result: string | null; created_at: string
}

export default function Logs() {
  const [logs, setLogs] = useState<Log[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')
  const [success, setSuccess] = useState('')
  const limit = 30

  useEffect(() => {
    let url = `/api/logs?page=${page}&limit=${limit}`
    if (action) url += `&action=${action}`
    if (success) url += `&success=${success}`
    api<any>(url).then(d => { setLogs(d.items); setTotal(d.total) }).catch(() => {})
  }, [page, action, success])

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white">Usage Logs</h2>
          <p className="text-sm text-dark-300 mt-1">{total} total</p>
        </div>
        <div className="flex gap-2">
          <select value={action} onChange={e => { setAction(e.target.value); setPage(1) }}
            className="px-3 py-2 bg-dark-700 border border-dark-500 rounded-lg text-sm text-white cursor-pointer focus:outline-none">
            <option value="">All Actions</option>
            <option value="VIDEO_GENERATION">Video</option>
            <option value="IMAGE_GENERATION">Image</option>
          </select>
          <select value={success} onChange={e => { setSuccess(e.target.value); setPage(1) }}
            className="px-3 py-2 bg-dark-700 border border-dark-500 rounded-lg text-sm text-white cursor-pointer focus:outline-none">
            <option value="">All Status</option>
            <option value="true">Success</option>
            <option value="false">Failed</option>
          </select>
        </div>
      </div>

      <div className="bg-dark-800 border border-dark-600 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-dark-600 text-dark-300">
              <th className="text-left px-4 py-3 font-medium">Time</th>
              <th className="text-left px-4 py-3 font-medium">Action</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-left px-4 py-3 font-medium">Token</th>
              <th className="text-left px-4 py-3 font-medium">IP</th>
              <th className="text-left px-4 py-3 font-medium">Time</th>
              <th className="text-left px-4 py-3 font-medium">Callback</th>
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <tr key={l.id} className="border-b border-dark-700 hover:bg-dark-700/50">
                <td className="px-4 py-3 text-dark-300 text-xs">{new Date(l.created_at + 'Z').toLocaleString()}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-1 rounded text-xs font-medium ${l.action.includes('VIDEO') ? 'bg-accent/20 text-accent-light' : 'bg-yellow-500/20 text-yellow-400'}`}>
                    {l.action.includes('VIDEO') ? 'Video' : 'Image'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-1 rounded text-xs font-medium ${l.success ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                    {l.success ? 'OK' : 'Fail'}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-dark-300">{l.token_preview || '-'}</td>
                <td className="px-4 py-3 text-dark-300 text-xs">{l.ip_address || '-'}</td>
                <td className="px-4 py-3 text-dark-200">{l.response_time_ms || '-'}</td>
                <td className="px-4 py-3 text-xs">
                  <span className={l.callback_result === 'success' ? 'text-green-400' : l.callback_result === 'failed' ? 'text-red-400' : 'text-dark-400'}>
                    {l.callback_result || '-'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!logs.length && <p className="px-5 py-12 text-center text-dark-400 text-sm">No logs yet.</p>}
      </div>

      <div className="flex items-center justify-between mt-4">
        <p className="text-sm text-dark-400">Page {page}</p>
        <div className="flex gap-2">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
            className="px-3 py-1.5 bg-dark-700 text-dark-200 rounded text-sm cursor-pointer disabled:opacity-50">Prev</button>
          <button onClick={() => setPage(p => p + 1)} disabled={logs.length < limit}
            className="px-3 py-1.5 bg-dark-700 text-dark-200 rounded text-sm cursor-pointer disabled:opacity-50">Next</button>
        </div>
      </div>
    </div>
  )
}
