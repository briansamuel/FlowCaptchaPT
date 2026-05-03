import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Stats {
  total_keys: number; active_keys: number; total_requests: number
  today_requests: number; hour_requests: number; success_rate: number
  avg_response_ms: number; queue: { total: number }
}

export default function Dashboard() {
  const [s, setS] = useState<Stats | null>(null)

  useEffect(() => {
    const load = () => api<Stats>('/api/dashboard/stats').then(setS).catch(() => {})
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [])

  const cards = s ? [
    { label: 'Total Requests', value: s.total_requests.toLocaleString(), sub: 'All time' },
    { label: 'Today', value: s.today_requests.toLocaleString(), sub: 'Requests today' },
    { label: 'Success Rate', value: s.success_rate + '%', sub: 'Overall' },
    { label: 'Avg Response', value: s.avg_response_ms.toLocaleString() + 'ms', sub: 'Successful' },
  ] : []

  return (
    <div>
      <h2 className="text-2xl font-bold text-white mb-1">Dashboard</h2>
      <p className="text-sm text-dark-300 mb-8">Service overview</p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        {cards.map(c => (
          <div key={c.label} className="bg-dark-800 border border-dark-600 rounded-xl p-5">
            <p className="text-sm text-dark-300 mb-2">{c.label}</p>
            <p className="text-3xl font-bold text-white">{c.value}</p>
            <p className="text-xs text-dark-400 mt-1">{c.sub}</p>
          </div>
        ))}
      </div>
      {s && (
        <div className="grid grid-cols-2 gap-5">
          <div className="bg-dark-800 border border-dark-600 rounded-xl p-5">
            <p className="text-sm font-semibold text-white mb-2">API Keys</p>
            <span className="text-2xl font-bold text-accent-light">{s.active_keys}</span>
            <span className="text-dark-400 text-sm ml-2">/ {s.total_keys} total</span>
          </div>
          <div className="bg-dark-800 border border-dark-600 rounded-xl p-5">
            <p className="text-sm font-semibold text-white mb-2">Queue</p>
            <span className="text-2xl font-bold text-yellow-400">{s.queue.total}</span>
            <span className="text-dark-400 text-sm ml-2">jobs</span>
          </div>
        </div>
      )}
    </div>
  )
}
