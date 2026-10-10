import { resolveAdminApiUrl, validateAdminSession } from './adminApi.js'
import { validateAccountSession } from './accountApi.js'

const messages = {
  configuration: 'The Coordinator API configuration is unsafe or invalid.',
  authentication: 'Please sign in again to use the Coordinator.',
  input: 'Describe your project in 10 to 5,000 characters.',
  response: 'The server returned invalid project details. They were not used.',
  network: 'Unable to reach the Coordinator. Check your connection and try again.',
  timeout: 'The Coordinator request timed out. Please try again.',
}

export class CoordinatorApiError extends Error {
  constructor(kind, status = 0) {
    super(kind === 'http'
      ? ({ 400: messages.input, 401: messages.authentication, 403: 'You do not have permission to use the Coordinator.', 422: messages.input, 429: 'Too many requests. Please wait before trying again.', 503: 'The Coordinator service is unavailable. Please try again later.' }[status] || 'The Coordinator request failed. Please try again.')
      : messages[kind] || messages.response)
    this.name = 'CoordinatorApiError'
    this.kind = kind
    this.status = status
  }
}

export function coordinatorErrorMessage(error) {
  return error instanceof CoordinatorApiError ? error.message : messages.response
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const string = (value, limit) => typeof value === 'string' && Array.from(value).length <= limit
const fields = ['project_name', 'objective', 'start_date', 'deadline']
const cancelled = () => new DOMException('Coordinator request cancelled.', 'AbortError')
function requireResponse(condition) { if (!condition) throw new CoordinatorApiError('response') }

function coordinatorSession(session, now) {
  try {
    return session?.role === 'admin'
      ? validateAdminSession(session, { now })
      : validateAccountSession(session, { now })
  } catch { throw new CoordinatorApiError('authentication', 401) }
}

function interpretationResponse(data) {
  requireResponse(object(data) && data.method === 'rule_based' && object(data.draft))
  requireResponse(Array.isArray(data.missing_fields) && data.missing_fields.every(field => fields.includes(field)) && new Set(data.missing_fields).size === data.missing_fields.length)
  requireResponse(Array.isArray(data.notes) && data.notes.every(note => string(note, 5000)))
  const draft = data.draft
  requireResponse(string(draft.objective, 3000) && string(draft.message, 1500) && Array.isArray(draft.resources))
  requireResponse(draft.project_name === undefined || string(draft.project_name, 200))
  for (const key of ['start_date', 'deadline']) requireResponse(draft[key] === undefined || typeof draft[key] === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(draft[key]))
  requireResponse(draft.resources.every(resource => object(resource) && string(resource.resource_id, 100) && resource.resource_id.trim() && string(resource.name, 200) && resource.name.trim() && Number.isSafeInteger(resource.capacity) && resource.capacity >= 1 && resource.capacity <= 100))
  requireResponse(new Set(draft.resources.map(resource => resource.resource_id)).size === draft.resources.length)
  // Only extraction fields can become the editable draft, never injected identity or tasks.
  return {
    method: data.method,
    draft: { ...Object.fromEntries([...fields, 'message'].filter(key => Object.hasOwn(draft, key)).map(key => [key, draft[key]])), resources: draft.resources.map(({ resource_id, name, capacity }) => ({ resource_id, name, capacity })) },
    missing_fields: [...data.missing_fields], notes: [...data.notes],
  }
}

export function createCoordinatorApi({ baseUrl = import.meta.env?.VITE_API_BASE_URL, origin = globalThis.location?.origin, fetchImpl = globalThis.fetch, timeoutMs = 30000, now = Date.now } = {}) {
  let root
  try { root = resolveAdminApiUrl({ configuredUrl: baseUrl, origin }) } catch { throw new CoordinatorApiError('configuration') }
  if (typeof fetchImpl !== 'function' || typeof now !== 'function' || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 2147483647) throw new CoordinatorApiError('configuration')

  async function transport(text, session, signal) {
    const controller = new AbortController()
    let timer
    let abort
    const interruption = new Promise((_, reject) => {
      abort = () => { controller.abort(); reject(cancelled()) }
      signal?.addEventListener('abort', abort, { once: true })
      timer = setTimeout(() => { controller.abort(); reject(new CoordinatorApiError('timeout')) }, timeoutMs)
    })
    const work = async () => {
      const url = `${root}/api/coordinator/interpret`
      let response
      try {
        response = await fetchImpl(url, {
          method: 'POST',
          headers: { Accept: 'application/json', Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }), credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal,
        })
      } catch (error) {
        if (controller.signal.aborted || error?.name === 'AbortError') throw cancelled()
        throw new CoordinatorApiError('network')
      }
      if (controller.signal.aborted) throw cancelled()
      requireResponse(response && !response.redirected && response.type !== 'opaqueredirect' && (!response.url || response.url === url))
      // Error bodies may include submitted text or credentials; never consume them.
      if (!response.ok) throw new CoordinatorApiError('http', response.status)
      requireResponse(response.status === 200)
      let data
      try {
        requireResponse(/^application\/(?:json|[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get('Content-Type') || ''))
        data = await response.json()
      } catch { throw new CoordinatorApiError('response') }
      if (controller.signal.aborted) throw cancelled()
      coordinatorSession(session, now())
      return interpretationResponse(data)
    }
    try { return await Promise.race([interruption, work()]) } catch (error) {
      if (error instanceof CoordinatorApiError) throw error
      if (error?.name === 'AbortError') throw cancelled()
      throw new CoordinatorApiError('response')
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
  }

  return {
    async interpretProjectInput(text, { session, signal, onUnauthorized } = {}) {
      if (signal?.aborted) throw cancelled()
      try {
        const current = coordinatorSession(session, now())
        if (!string(text, 5000) || Array.from(text.trim()).length < 10) throw new CoordinatorApiError('input')
        return await transport(text, current, signal)
      } catch (error) {
        if (signal?.aborted || error?.name === 'AbortError') throw cancelled()
        const failure = error instanceof CoordinatorApiError ? error : new CoordinatorApiError('response')
        if (failure.status === 401) {
          // Keep the exact originating object: the parent must not clear a newer login.
          try { Promise.resolve(onUnauthorized?.(session)).catch(() => {}) } catch { /* Cleanup errors must not expose data or replace the safe API error. */ }
        }
        throw failure
      }
    },
  }
}

// Browser interpretation uses only the signed-in bearer. /request is service-only.
export const interpretProjectInput = (...args) => createCoordinatorApi().interpretProjectInput(...args)
