const messages = {
  configuration: 'The admin API URL is unsafe or invalid. Use a server-root HTTPS URL or local development server.',
  response: 'The server returned an invalid or mismatched admin response. The response was not used.',
  expired: 'Your administrator session has expired. Please sign in again.',
  network: 'Unable to reach the admin API. Check its availability and network configuration.',
  timeout: 'The admin request timed out. Please try again.',
}

export class AdminApiError extends Error {
  constructor(kind, status = 0) {
    const message = kind === 'http'
      ? ({ 401: 'Authentication is required. Please sign in again.', 403: 'The server denied this administrator operation.', 429: 'Too many requests. Please wait before trying again.' }[status] || 'The admin request failed. Please try again.')
      : messages[kind] || messages.response
    super(message)
    this.name = 'AdminApiError'
    this.kind = kind
    this.status = status
  }
}

export function adminErrorMessage(error, { login = false } = {}) {
  if (error instanceof AdminApiError) {
    if (login && error.status === 401 && error.kind === 'http') return 'Invalid username or password.'
    return error.message
  }
  return messages.response
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const codePointLength = value => Array.from(value).length
const usernameValid = value => typeof value === 'string' && codePointLength(value) > 0 && codePointLength(value) <= 100 && value === value.trim() && Array.from(value).every(character => character.codePointAt(0) >= 32 && character.codePointAt(0) !== 127)
const futureExpiry = (value, now) => Number.isSafeInteger(value) && Number.isSafeInteger(value * 1000) && value > Math.floor(now / 1000)
const cancelled = () => new DOMException('Admin request cancelled.', 'AbortError')
function requireResponse(condition) {
  if (!condition) throw new AdminApiError('response')
}

export function resolveAdminApiUrl({ configuredUrl, origin = globalThis.location?.origin } = {}) {
  const candidate = configuredUrl === undefined ? (origin || 'http://127.0.0.1:8000') : configuredUrl
  let url
  try {
    if (typeof candidate !== 'string' || candidate !== candidate.trim() || !/^https?:\/\/[^/?#\\@]+\/?$/i.test(candidate) || /\s/.test(candidate)) throw new Error()
    url = new URL(candidate)
  } catch {
    throw new AdminApiError('configuration')
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.username || url.password || url.pathname !== '/' || url.search || url.hash || !(url.protocol === 'https:' || url.protocol === 'http:' && loopback)) {
    throw new AdminApiError('configuration')
  }
  return url.origin
}

function jwtClaims(token) {
  requireResponse(typeof token === 'string' && token.length <= 8192 && /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token))
  function decode(segment) {
    const base64 = segment.replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')), char => char.charCodeAt(0))
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  }
  try {
    const [headerPart, payloadPart] = token.split('.')
    const header = decode(headerPart)
    const payload = decode(payloadPart)
    requireResponse(object(header) && typeof header.alg === 'string' && /^[A-Za-z0-9-]+$/.test(header.alg) && header.alg.toLowerCase() !== 'none' && (header.typ === undefined || header.typ === 'JWT') && object(payload))
    return payload
  } catch {
    throw new AdminApiError('response')
  }
}

export function validateAdminSession(session, { now = Date.now(), expectedUsername } = {}) {
  requireResponse(object(session) && session.token_type === 'bearer' && session.role === 'admin' && usernameValid(session.username) && Number.isSafeInteger(session.expires_at) && Number.isSafeInteger(session.expires_at * 1000))
  // These are consistency checks, not signature verification. The protected
  // session endpoint is the authority before any private UI is displayed.
  const claims = jwtClaims(session.access_token)
  const canonicalUsername = /^[a-z0-9][a-z0-9_.-]{2,49}$/.test(session.username)
  requireResponse(claims.token_use !== 'account_access' || canonicalUsername)
  const managedUsername = claims.token_use === 'account_access' && typeof claims.account_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(claims.account_id) && canonicalUsername
  requireResponse(expectedUsername === undefined || session.username === expectedUsername || managedUsername && typeof expectedUsername === 'string' && session.username === expectedUsername.toLowerCase())
  requireResponse(claims.sub === session.username && claims.role === session.role && claims.exp === session.expires_at)
  if (!futureExpiry(session.expires_at, now)) throw new AdminApiError('expired', 401)
  return Object.freeze({ access_token: session.access_token, token_type: 'bearer', username: session.username, role: 'admin', expires_at: session.expires_at })
}

export function isAdminSessionValid(session, now = Date.now()) {
  try { validateAdminSession(session, { now }); return true } catch { return false }
}

function validateSessionResponse(data, session, now) {
  requireResponse(object(data) && !Object.hasOwn(data, 'access_token') && data.username === session.username && data.role === 'admin' && data.expires_at === session.expires_at)
  validateAdminSession(session, { now })
  return Object.freeze({ username: data.username, role: 'admin', expires_at: data.expires_at })
}

const identifier = value => typeof value === 'string' && value.trim().length > 0
const count = value => Number.isSafeInteger(value) && value >= 0
const optionalString = value => value == null || typeof value === 'string'
const optionalIdentifier = value => value == null || identifier(value)

export function validateAdminKnowledgeResponse(data, projectId, collection, documentId) {
  const belongs = value => object(value) && (value.project_id === undefined || value.project_id === projectId)
  const metadataValid = value => value == null || belongs(value)
  requireResponse(identifier(projectId) && belongs(data) && data.project_id === projectId && ['documents', 'chunks', 'results'].includes(collection) && Array.isArray(data[collection]))
  requireResponse(data[collection].every(item => belongs(item) && metadataValid(item.metadata)))

  if (collection === 'documents') {
    requireResponse(count(data.total_documents) && count(data.total_chunks))
    requireResponse(data.documents.every(document => identifier(document.document_id) && typeof document.name === 'string' && count(document.chunks_count) && optionalString(document.source) && optionalString(document.document_hash)))
  } else if (collection === 'chunks') {
    requireResponse(identifier(documentId) && data.document_id === documentId && count(data.total_chunks))
    requireResponse(data.chunks.every(chunk => {
      // Legacy Chroma metadata can store the displayed index as a string.
      const indexValid = count(chunk.chunk_index) || typeof chunk.chunk_index === 'string'
      return identifier(chunk.chunk_id) && indexValid && optionalString(chunk.text) && (chunk.document_id === undefined || chunk.document_id === documentId) && (chunk.metadata?.document_id === undefined || chunk.metadata.document_id === documentId)
    }))
  } else {
    // Planning records have record_id instead of chunk_id and no score.
    requireResponse(data.results.every(result => optionalIdentifier(result.chunk_id) && optionalIdentifier(result.record_id) && optionalString(result.knowledge_type) && optionalString(result.source) && optionalString(result.content) && (result.score == null || typeof result.score === 'number' && Number.isFinite(result.score))))
  }
  return data
}

export function createAdminApi({ baseUrl = import.meta.env?.VITE_API_BASE_URL, origin = globalThis.location?.origin, fetchImpl = globalThis.fetch, timeoutMs = 30000, now = Date.now } = {}) {
  const root = resolveAdminApiUrl({ configuredUrl: baseUrl, origin })
  if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 2147483647 || typeof now !== 'function') throw new AdminApiError('configuration')

  async function transport(path, { session, method = 'GET', body, signal, validate = value => value } = {}) {
    if (signal?.aborted) throw cancelled()
    if (session) validateAdminSession(session, { now: now() })
    const controller = new AbortController()
    let timer
    let abort
    // Racing the entire fetch + body read also bounds transports that ignore abort.
    const interruption = new Promise((_, reject) => {
      abort = () => {
        controller.abort()
        reject(cancelled())
      }
      signal?.addEventListener('abort', abort, { once: true })
      timer = setTimeout(() => {
        controller.abort()
        reject(new AdminApiError('timeout'))
      }, timeoutMs)
    })
    const work = async () => {
      const multipart = body instanceof FormData
      const init = {
        method,
        headers: {
          Accept: 'application/json',
          ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
          ...(body !== undefined && !multipart ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(body !== undefined ? { body: multipart ? body : JSON.stringify(body) } : {}),
        credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
        signal: controller.signal,
      }
      let response
      try { response = await fetchImpl(`${root}${path}`, init) } catch (error) {
        if (controller.signal.aborted || error?.name === 'AbortError') throw cancelled()
        throw new AdminApiError('network')
      }
      if (controller.signal.aborted) throw cancelled()
      requireResponse(response && !response.redirected && response.type !== 'opaqueredirect' && (!response.url || response.url === `${root}${path}`))
      // Never read/echo error bodies: validation details may contain credentials.
      if (!response.ok) throw new AdminApiError('http', response.status)
      let data
      try {
        requireResponse(/^application\/(?:json|[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get('Content-Type') || ''))
        data = await response.json()
      } catch {
        throw new AdminApiError('response')
      }
      if (controller.signal.aborted) throw cancelled()
      if (session) validateAdminSession(session, { now: now() })
      return validate(data)
    }
    try {
      return await Promise.race([interruption, work()])
    } catch (error) {
      if (error instanceof AdminApiError) throw error
      if (error?.name === 'AbortError') throw cancelled()
      throw new AdminApiError('response')
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
  }

  return {
    async login({ username, password } = {}, { signal } = {}) {
      if (!usernameValid(username) || typeof password !== 'string' || codePointLength(password) < 1 || codePointLength(password) > 256) throw new AdminApiError('response')
      return transport('/api/auth/login', {
        method: 'POST', body: { username, password }, signal,
        validate: data => validateAdminSession(data, { now: now(), expectedUsername: username }),
      })
    },
    async verifySession(session, { signal } = {}) {
      validateAdminSession(session, { now: now() })
      return transport('/api/auth/session', { session, signal, validate: data => validateSessionResponse(data, session, now()) })
    },
    async request(path, session, { method = 'GET', body, signal } = {}) {
      let url
      try {
        if (typeof path !== 'string' || !path.startsWith('/api/risk') || /[\s\\#]/.test(path)) throw new Error()
        url = new URL(path, root)
        if (url.origin !== root || !(url.pathname === '/api/risk' || url.pathname.startsWith('/api/risk/')) || url.hash || url.username || url.password) throw new Error()
      } catch {
        throw new AdminApiError('configuration')
      }
      const deleteDocument = method === 'DELETE' && /^\/api\/risk\/knowledge\/documents\/[^/]+$/.test(url.pathname)
      if (!['GET', 'POST'].includes(method) && !deleteDocument || ['GET', 'DELETE'].includes(method) && body !== undefined) throw new AdminApiError('configuration')
      validateAdminSession(session, { now: now() })
      return transport(`${url.pathname}${url.search}`, { session, method, body, signal, validate: data => { requireResponse(object(data)); return data } })
    },
  }
}

// Lazy configuration errors stay in the admin UI instead of breaking public routes.
export const adminApi = {
  login: async (...args) => createAdminApi().login(...args),
  verifySession: async (...args) => createAdminApi().verifySession(...args),
  request: async (...args) => createAdminApi().request(...args),
}
