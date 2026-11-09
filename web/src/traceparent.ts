// W3C Trace Context for API calls (task 11): each call sends a fresh `traceparent`, so the
// API's trace in Dash0 starts from the browser's request. No SDK: 16 random bytes for the
// trace id, 8 for the parent span id, flags 01 (sampled).

const hex = (bytes: Uint8Array): string =>
  Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')

function randomHex(size: number): string {
  const bytes = new Uint8Array(size)
  do {
    crypto.getRandomValues(bytes)
  } while (bytes.every((b) => b === 0)) // all-zero ids are invalid
  return hex(bytes)
}

export function traceparent(): string {
  return `00-${randomHex(16)}-${randomHex(8)}-01`
}

// Request options with a `traceparent` header added; use for every /api fetch.
export function withTrace(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers)
  headers.set('traceparent', traceparent())
  return { ...init, headers }
}
