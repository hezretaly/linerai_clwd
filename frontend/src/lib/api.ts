export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(status: number, message: string, payload: unknown) {
    super(message)
    this.status = status
    this.payload = payload
  }

  /** A 503 from an integration that has no credentials, carrying the key names. */
  get notConfigured(): { integration: string; missing: string[]; detail: string } | null {
    const p = this.payload as Record<string, unknown> | null
    const body = (p && typeof p === 'object' && 'detail' in p && typeof p.detail === 'object'
      ? (p.detail as Record<string, unknown>)
      : p) as Record<string, unknown> | null
    if (body && body.error === 'not_configured') {
      return {
        integration: String(body.integration ?? ''),
        missing: (body.missing as string[]) ?? [],
        detail: String(body.detail ?? ''),
      }
    }
    return null
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    /* A multipart body must set its own Content-Type: only the browser knows
     * the boundary, and naming the type here strips it, which the server sees
     * as a body with no parts (422). */
    headers:
      init.body && !(init.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : undefined,
    ...init,
  })

  const text = await response.text()
  let payload: unknown = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = text
  }

  if (!response.ok) {
    const p = payload as Record<string, unknown> | null
    const detail = p && typeof p === 'object' ? p.detail : null
    const message =
      typeof detail === 'string' ? detail : `${init.method ?? 'GET'} ${path} failed`
    throw new ApiError(response.status, message, payload)
  }
  return payload as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form as unknown as BodyInit })
  },
}

/** SSE reader for the buyer chat stream. */
export async function streamMessages(
  conversationId: string,
  body: { content?: string; rail_id?: string },
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const response = await fetch(`/api/chat/sessions/${conversationId}/messages`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, 'Chat stream failed', await response.text())
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      let name = ''
      let data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event: ')) name = line.slice(7)
        else if (line.startsWith('data: ')) data = line.slice(6)
      }
      if (name && data) onEvent(name, JSON.parse(data))
    }
  }
}
