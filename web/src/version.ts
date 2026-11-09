import { withTrace } from './traceparent'

// The deployed build's version (the commit's short SHA), from GET /api/version.
// Returns null when the API can't say, so the page still renders.
export async function fetchVersion(fetchFn: typeof fetch = fetch): Promise<string | null> {
  try {
    const response = await fetchFn('/api/version', withTrace())
    if (!response.ok) return null
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'version' in body && typeof body.version === 'string') {
      return body.version
    }
    return null
  } catch {
    return null
  }
}
