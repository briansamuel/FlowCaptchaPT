import { NavLink, Outlet } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { getAdminToken, setAdminToken, api } from '../lib/api'

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/keys', label: 'API Keys' },
  { to: '/cookies', label: 'Cookies' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs' },
]

export default function Layout() {
  const [authed, setAuthed] = useState(!!getAdminToken())
  const [input, setInput] = useState('')
  const [err, setErr] = useState('')

  async function login() {
    setAdminToken(input)
    try {
      await api('/api/dashboard/stats')
      setAuthed(true)
    } catch {
      setErr('Invalid token')
      localStorage.removeItem('fc_admin_token')
    }
  }

  if (!authed) return (
    <div className="min-h-screen flex items-center justify-center bg-dark-900">
      <div className="bg-dark-800 border border-dark-600 rounded-2xl p-8 w-full max-w-sm">
        <h2 className="text-xl font-bold text-white mb-2">Admin Login</h2>
        <p className="text-sm text-dark-300 mb-6">Enter admin token</p>
        <input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && login()}
          type="password" placeholder="Admin token"
          className="w-full px-4 py-3 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm mb-4 focus:outline-none focus:border-accent" />
        <button onClick={login}
          className="w-full py-3 bg-accent hover:bg-accent-dark text-white rounded-lg text-sm font-medium cursor-pointer">
          Login</button>
        {err && <p className="text-red-400 text-xs mt-3">{err}</p>}
      </div>
    </div>
  )

  return (
    <div className="min-h-screen bg-dark-900 text-dark-100 flex">
      <aside className="w-56 bg-dark-800 border-r border-dark-600 flex flex-col fixed h-full">
        <div className="p-5 border-b border-dark-600">
          <h1 className="text-lg font-bold text-white">FlowCaptchaPT</h1>
          <p className="text-xs text-dark-400 mt-1">Captcha Token Service</p>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {links.map(l => (
            <NavLink key={l.to} to={l.to} end
              className={({ isActive }) =>
                `block px-3 py-2.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                  isActive ? 'bg-accent/10 text-accent-light' : 'text-dark-300 hover:text-white hover:bg-dark-700'
                }`}>
              {l.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="ml-56 flex-1 p-8"><Outlet /></main>
    </div>
  )
}
