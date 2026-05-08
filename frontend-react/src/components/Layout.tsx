import { NavLink, Outlet } from 'react-router-dom'

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/keys', label: 'API Keys' },
  { to: '/cookies', label: 'Cookies' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs' },
]

export default function Layout() {
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
