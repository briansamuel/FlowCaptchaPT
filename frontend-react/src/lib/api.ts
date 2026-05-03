const BASE = ''

export function getAdminToken(): string {
  return localStorage.getItem('fc_admin_token') || ''
}
export function setAdminToken(t: string) {
  localStorage.setItem('fc_admin_token', t)
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = getAdminToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...opts.headers as any }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(BASE + path, { ...opts, headers })
  if (res.status === 401 || res.status === 403) throw new Error('Unauthorized')
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}
