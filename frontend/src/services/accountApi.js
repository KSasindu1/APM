import { resolveAdminApiUrl, validateAdminSession } from './adminApi.js'

// Public build-time configuration; must match the backend ACCOUNT_JWT_AUDIENCE.
export const ACCOUNT_JWT_AUDIENCE = import.meta.env?.VITE_ACCOUNT_JWT_AUDIENCE ?? 'apm-accounts'

const messages = {
  configuration: 'The account API configuration is unsafe or invalid. Use a server-root HTTPS URL or local development server.',
  response: 'The server returned an invalid or mismatched account response. The response was not used.',
  input: 'Check the username, password and invitation fields before trying again.',
  expired: 'Your session has expired. Please sign in again.',
  network: 'Unable to reach the account API. Check your connection and try again.',
  timeout: 'The account request timed out. Please try again.',
}

export class AccountApiError extends Error {
  constructor(kind, status = 0) {
    super(kind === 'http'
      ? ({ 400: 'Check the account details and try again.', 401: 'Authentication is required. Please sign in again.', 403: 'The server denied this operation. Administrator invitations are restricted.', 409: 'This username or invitation is unavailable. Check the details before trying again.', 422: 'Check the account details and invitation before trying again.', 429: 'Too many requests. Please wait before trying again.', 503: 'The account service is unavailable. Ask the administrator to check the backend and account database setup, then try again.' }[status] || 'The account request failed. Please try again.')
      : messages[kind] || messages.response)
    this.name = 'AccountApiError'
    this.kind = kind
    this.status = status
  }
}

export function accountErrorMessage(error, { login = false } = {}) {
  if (!(error instanceof AccountApiError)) return messages.response
  if (login && error.kind === 'http' && error.status === 401) return 'Invalid username or password.'
  return error.message
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const usernameValid = value => typeof value === 'string' && /^[a-z0-9][a-z0-9_.-]{2,49}$/.test(value)
const audienceValid = value => typeof value === 'string' && value.trim().length > 0
const wellFormedUnicode = value => Array.from(value).every(character => {
  const point = character.codePointAt(0)
  return point < 0xd800 || point > 0xdfff
})
const uuidValid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
const futureExpiry = (value, now) => Number.isSafeInteger(value) && Number.isSafeInteger(value * 1000) && value * 1000 <= 8640000000000000 && value > Math.floor(now / 1000)
// A bounded URL-safe secret, not a message, URL, or short human-readable code.
// Format checks cannot prove randomness; entropy generation is the server's job.
const invitationValid = value => typeof value === 'string' && /^[A-Za-z0-9_-]{32,256}$/.test(value) && new Set(value).size >= 8
const exactKeys = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const cancelled = () => new DOMException('Account request cancelled.', 'AbortError')
function requireResponse(condition) { if (!condition) throw new AccountApiError('response') }

function jwtClaims(token) {
  requireResponse(typeof token === 'string' && token.length <= 8192 && /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token))
  const decode = segment => {
    const base64 = segment.replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')), char => char.charCodeAt(0))
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  }
  try {
    const [head, payload] = token.split('.')
    const header = decode(head)
    const claims = decode(payload)
    requireResponse(object(header) && typeof header.alg === 'string' && /^[A-Za-z0-9-]+$/.test(header.alg) && header.alg.toLowerCase() !== 'none' && (header.typ === undefined || header.typ === 'JWT') && object(claims))
    return claims
  } catch { throw new AccountApiError('response') }
}

export function validateAccountSession(session, { now = Date.now(), expectedUsername, audience = ACCOUNT_JWT_AUDIENCE, issuer } = {}) {
  requireResponse(object(session) && session.token_type === 'bearer' && usernameValid(session.username) && uuidValid(session.user_id) && ['user', 'admin'].includes(session.role))
  requireResponse(expectedUsername === undefined || typeof expectedUsername === 'string' && session.username === expectedUsername.toLowerCase())
  // Consistency only: the protected session endpoint, not decoded JWT claims,
  // authorizes access. Never use this check as signature verification.
  const claims = jwtClaims(session.access_token)
  requireResponse(claims.sub === session.username && claims.account_id === session.user_id && claims.role === session.role && claims.exp === session.expires_at && claims.token_use === 'account_access')
  requireResponse(audienceValid(audience) && claims.aud === audience && typeof claims.iss === 'string' && claims.iss.trim().length > 0 && (issuer === undefined || claims.iss === issuer))
  requireResponse(Number.isSafeInteger(claims.iat) && claims.iat >= 0 && claims.iat <= Math.floor(now / 1000) && claims.iat < claims.exp && Number.isSafeInteger(session.expires_at) && Number.isSafeInteger(session.expires_at * 1000))
  if (!futureExpiry(session.expires_at, now)) throw new AccountApiError('expired', 401)
  return Object.freeze({ access_token: session.access_token, token_type: 'bearer', username: session.username, role: session.role, user_id: session.user_id, expires_at: session.expires_at })
}

export function isAccountSessionValid(session, now = Date.now(), options = {}) {
  try { validateAccountSession(session, { ...options, now }); return true } catch { return false }
}

function credentials(input, minimum) {
  if (!object(input) || !usernameValid(input.username) || typeof input.password !== 'string' || Array.from(input.password).length < minimum || Array.from(input.password).length > 256) throw new AccountApiError('input')
  // Match Python isspace() plus BOM: White_Space, U+001C–U+001F,
  // and U+FEFF (already covered by JavaScript's \s).
  // eslint-disable-next-line no-control-regex -- Intentional C0 whitespace in the signup-only blank policy.
  if (minimum === 12 && (!/[^\s\p{White_Space}\u001c-\u001f]/u.test(input.password) || !wellFormedUnicode(input.password))) throw new AccountApiError('input')
  return { username: input.username, password: input.password }
}

function registration(data, username, role) {
  requireResponse(exactKeys(data, ['user_id', 'username', 'role']) && uuidValid(data.user_id) && usernameValid(data.username) && data.username === username && data.role === role)
  return Object.freeze({ user_id: data.user_id, username: data.username, role: data.role })
}

export function createAccountApi({ baseUrl = import.meta.env?.VITE_API_BASE_URL, origin = globalThis.location?.origin, fetchImpl = globalThis.fetch, timeoutMs = 30000, now = Date.now, audience = ACCOUNT_JWT_AUDIENCE, issuer } = {}) {
  let root
  try { root = resolveAdminApiUrl({ configuredUrl: baseUrl, origin }) } catch { throw new AccountApiError('configuration') }
  if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 2147483647 || typeof now !== 'function' || !audienceValid(audience) || issuer !== undefined && (typeof issuer !== 'string' || !issuer.trim())) throw new AccountApiError('configuration')
  const validateSession = (session, extra = {}) => validateAccountSession(session, { now: now(), audience, issuer, ...extra })
  function validateInvitationSession(session) {
    try { return validateAdminSession(session, { now: now() }) } catch { throw new AccountApiError('expired', 401) }
  }

  async function transport(path, { session, method = 'GET', body, signal, status = 200, validate, checkSession = validateSession } = {}) {
    if (signal?.aborted) throw cancelled()
    if (session) session = checkSession(session)
    const controller = new AbortController()
    let timer
    let abort
    const interruption = new Promise((_, reject) => {
      abort = () => { controller.abort(); reject(cancelled()) }
      signal?.addEventListener('abort', abort, { once: true })
      timer = setTimeout(() => { controller.abort(); reject(new AccountApiError('timeout')) }, timeoutMs)
    })
    const work = async () => {
      let response
      try {
        response = await fetchImpl(`${root}${path}`, {
          method,
          headers: { Accept: 'application/json', ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
          credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal,
        })
      } catch (error) {
        if (controller.signal.aborted || error?.name === 'AbortError') throw cancelled()
        throw new AccountApiError('network')
      }
      if (controller.signal.aborted) throw cancelled()
      requireResponse(response && !response.redirected && response.type !== 'opaqueredirect' && (!response.url || response.url === `${root}${path}`))
      // Error bodies may contain credentials; never read or expose them.
      if (!response.ok) throw new AccountApiError('http', response.status)
      requireResponse(Array.isArray(status) ? status.includes(response.status) : response.status === status)
      let data
      try {
        requireResponse(/^application\/(?:json|[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get('Content-Type') || ''))
        data = await response.json()
      } catch { throw new AccountApiError('response') }
      if (controller.signal.aborted) throw cancelled()
      if (session) checkSession(session)
      return validate(data)
    }
    try { return await Promise.race([interruption, work()]) } catch (error) {
      if (error instanceof AccountApiError) throw error
      if (error?.name === 'AbortError') throw cancelled()
      throw new AccountApiError('response')
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
  }

  return {
    async signup(input, { signal } = {}) {
      const body = credentials(input, 12)
      // Explicit projection ignores injected role/token/transport parameters.
      return transport('/api/accounts/signup', { method: 'POST', body, signal, status: 201, validate: data => registration(data, body.username, 'user') })
    },
    async adminSignup(input, { signal } = {}) {
      const body = credentials(input, 12)
      if (!invitationValid(input.invitation_code)) throw new AccountApiError('input')
      body.invitation_code = input.invitation_code
      return transport('/api/accounts/admin-signup', { method: 'POST', body, signal, status: 201, validate: data => registration(data, body.username, 'admin') })
    },
    async login(input, { signal } = {}) {
      const body = credentials(input, 1)
      return transport('/api/accounts/login', { method: 'POST', body, signal, validate: data => validateSession(data, { expectedUsername: body.username }) })
    },
    async verifySession(session, { signal } = {}) {
      const current = validateSession(session)
      return transport('/api/accounts/session', { session: current, signal, validate: data => {
        requireResponse(exactKeys(data, ['user_id', 'username', 'role', 'expires_at']) && data.user_id === current.user_id && data.username === current.username && data.role === current.role && data.expires_at === current.expires_at)
        return Object.freeze({ user_id: data.user_id, username: data.username, role: data.role, expires_at: data.expires_at })
      } })
    },
    async createAdminInvitation(session, input, { signal } = {}) {
      if (!object(input) || !usernameValid(input.username)) throw new AccountApiError('input')
      const body = { username: input.username }
      const current = validateInvitationSession(session)
      return transport('/api/accounts/admin-invitations', { session: current, checkSession: validateInvitationSession, method: 'POST', body, signal, status: [200, 201], validate: data => {
        requireResponse(exactKeys(data, ['invitation_code', 'username', 'expires_at']) && data.username === body.username && invitationValid(data.invitation_code) && futureExpiry(data.expires_at, now()))
        return Object.freeze({ invitation_code: data.invitation_code, username: data.username, expires_at: data.expires_at })
      } })
    },
  }
}

// Lazy construction keeps configuration failures inside the requesting UI.
export const accountApi = Object.fromEntries(['signup', 'login', 'adminSignup', 'verifySession', 'createAdminInvitation'].map(method => [method, async (...args) => createAccountApi()[method](...args)]))
