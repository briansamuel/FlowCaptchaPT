import { useState } from 'react'

export default function Cookies() {
  const [cookieJson, setCookieJson] = useState('')
  const [url, setUrl] = useState('https://labs.google')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function importCookies() {
    if (!cookieJson.trim()) return
    setLoading(true)
    setResult(null)

    let payload: { url: string; cookies: any[] }
    try {
      const parsed = JSON.parse(cookieJson)
      if (Array.isArray(parsed)) {
        payload = { url: url || 'https://labs.google', cookies: parsed }
      } else if (parsed.cookies) {
        payload = { url: url || parsed.url || 'https://labs.google', cookies: parsed.cookies }
      } else {
        setResult({ ok: false, message: 'Invalid format. Expected array of cookies or {url, cookies}' })
        setLoading(false)
        return
      }
    } catch (e: any) {
      setResult({ ok: false, message: 'Invalid JSON: ' + e.message })
      setLoading(false)
      return
    }

    try {
      const res = await fetch('/api/captcha/import-cookies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (data.ok) {
        setResult({ ok: true, message: `Imported ${data.imported} cookies, verified ${data.verified} in profile.` })
        setCookieJson('')
      } else {
        setResult({ ok: false, message: data.detail || data.error || 'Import failed' })
      }
    } catch (e: any) {
      setResult({ ok: false, message: 'Error: ' + e.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl">
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-white">Import Cookies</h2>
        <p className="text-sm text-dark-300 mt-1">Inject login cookies into Chrome profile for authenticated captcha tokens</p>
      </div>

      {/* Instructions */}
      <div className="bg-dark-800 border border-yellow-500/30 rounded-xl p-5 mb-6">
        <h3 className="text-sm font-semibold text-yellow-400 mb-2">How to get cookies</h3>
        <ol className="text-sm text-dark-200 space-y-1.5 list-decimal list-inside">
          <li>Login to <a href="https://labs.google/fx/tools/flow" target="_blank" rel="noreferrer" className="text-accent-light hover:underline">labs.google/fx/tools/flow</a> in your browser</li>
          <li>Install <a href="https://chromewebstore.google.com/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg" target="_blank" rel="noreferrer" className="text-accent-light hover:underline">EditThisCookie</a> extension</li>
          <li>Click the extension icon, then "Export" (copies JSON to clipboard)</li>
          <li>Paste the JSON below and click Import</li>
        </ol>
      </div>

      {/* Cookie JSON Input */}
      <div className="bg-dark-800 border border-dark-600 rounded-xl p-5 mb-6">
        <label className="block text-sm font-semibold text-white mb-2">Cookie JSON</label>
        <p className="text-xs text-dark-400 mb-3">Paste the full JSON from EditThisCookie export, or the format: {`{"url":"...", "cookies":[...]}`}</p>
        <textarea
          value={cookieJson}
          onChange={e => setCookieJson(e.target.value)}
          rows={10}
          placeholder='[{"name":"SID","value":"...","domain":".google.com",...}]'
          className="w-full px-4 py-3 bg-dark-700 border border-dark-500 rounded-lg text-white font-mono text-xs placeholder-dark-500 focus:outline-none focus:border-accent resize-y"
        />
      </div>

      {/* Target URL */}
      <div className="bg-dark-800 border border-dark-600 rounded-xl p-5 mb-6">
        <label className="block text-sm font-semibold text-white mb-2">Target URL</label>
        <input
          value={url}
          onChange={e => setUrl(e.target.value)}
          type="text"
          placeholder="https://labs.google"
          className="w-full px-4 py-2.5 bg-dark-700 border border-dark-500 rounded-lg text-white text-sm focus:outline-none focus:border-accent"
        />
        <p className="text-xs text-dark-400 mt-1">URL domain the cookies belong to</p>
      </div>

      {/* Quick Templates */}
      <div className="bg-dark-800 border border-dark-600 rounded-xl p-5 mb-6">
        <h3 className="text-sm font-semibold text-white mb-3">Quick Templates</h3>
        <div className="flex gap-2">
          <button onClick={() => setUrl('https://labs.google')}
            className="px-4 py-2 bg-dark-700 hover:bg-dark-600 rounded-lg text-sm text-dark-200 transition-colors cursor-pointer">
            <span className="text-accent-light font-medium">labs.google</span>
          </button>
          <button onClick={() => setUrl('https://www.google.com')}
            className="px-4 py-2 bg-dark-700 hover:bg-dark-600 rounded-lg text-sm text-dark-200 transition-colors cursor-pointer">
            <span className="text-accent-light font-medium">google.com</span>
          </button>
        </div>
      </div>

      {/* Import Button */}
      <button
        onClick={importCookies}
        disabled={loading || !cookieJson.trim()}
        className="w-full py-3 bg-accent hover:bg-accent-dark disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors cursor-pointer mb-4"
      >
        {loading ? 'Importing...' : 'Import Cookies'}
      </button>

      {/* Result */}
      {result && (
        <div className={`bg-dark-800 border rounded-xl p-5 ${result.ok ? 'border-green-500/30' : 'border-red-500/30'}`}>
          <p className={`text-sm ${result.ok ? 'text-green-400' : 'text-red-400'}`}>{result.message}</p>
        </div>
      )}
    </div>
  )
}
