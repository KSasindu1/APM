import { resolveAdminApiUrl, validateAdminSession } from './adminApi.js'
import { validateAccountSession } from './accountApi.js'

const messages = {
  configuration: 'The Planning API configuration is unsafe or invalid.',
  authentication: 'Please sign in again to access your projects.',
  input: 'Check the project ID, project details, and owner username.',
  response: 'The server returned an invalid project response. It was not used.',
  network: 'Unable to reach the Planning service. Check your connection and try again.',
  timeout: 'The Planning request timed out. Please try again.',
  generation_timeout: 'Schedule generation may still finish. Check your project list before submitting the project again.',
}

export class PlanningApiError extends Error {
  constructor(kind, status = 0) {
    super(kind === 'http'
      ? ({ 400: 'Check the project details and try again.', 401: messages.authentication, 403: 'You do not have permission to perform this project operation.', 404: 'This project is unavailable to your account.', 409: 'This project ID is already in use. Choose another ID.', 422: messages.input, 429: 'Too many requests. Please wait before trying again.', 503: 'The project service is unavailable. Its database setup or connection needs attention.' }[status] || 'The project request failed. Please try again.')
      : messages[kind] || messages.response)
    this.name = 'PlanningApiError'
    this.kind = kind
    this.status = status
  }
}

export function planningErrorMessage(error) {
  return error instanceof PlanningApiError ? error.message : messages.response
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const cancelled = () => new DOMException('Planning request cancelled.', 'AbortError')
function requireResponse(condition) { if (!condition) throw new PlanningApiError('response') }

function projectId(value) {
  if (typeof value !== 'string') throw new PlanningApiError('input')
  const id = value.trim()
  // eslint-disable-next-line no-control-regex -- Identifiers must not contain C0 controls.
  if (!id || Array.from(id).length > 100 || /[\u0000-\u001f\u007f]/.test(id)) throw new PlanningApiError('input')
  try { encodeURIComponent(id) } catch { throw new PlanningApiError('input') }
  return id
}

function projectSession(session, now) {
  try {
    return session?.role === 'admin'
      ? validateAdminSession(session, { now })
      : validateAccountSession(session, { now })
  } catch { throw new PlanningApiError('authentication', 401) }
}

function scheduleResponse(data, expectedId) {
  requireResponse(object(data) && typeof data.project_id === 'string' && data.project_id.trim().length > 0 && typeof data.project_name === 'string' && Array.isArray(data.schedule))
  requireResponse(expectedId === undefined || data.project_id === expectedId)
  return data
}

export function buildPlanningRequest(form) {
  if (!object(form)) throw new PlanningApiError('input')
  return {
    project_id: form.project_id,
    project_name: form.project_name,
    objective: form.objective,
    start_date: form.start_date,
    deadline: form.deadline,
    tasks: form.tasks || [],
    resources: form.resources || [],
    risks: form.risks || [],
    use_llm: form.use_llm !== false,
  }
}

export function createPlanningApi({ baseUrl = import.meta.env?.VITE_API_BASE_URL, origin = globalThis.location?.origin, fetchImpl = globalThis.fetch, timeoutMs = 30000, generationTimeoutMs = 180000, now = Date.now } = {}) {
  let root
  try { root = resolveAdminApiUrl({ configuredUrl: baseUrl, origin }) } catch { throw new PlanningApiError('configuration') }
  if (typeof fetchImpl !== 'function' || typeof now !== 'function' || [timeoutMs, generationTimeoutMs].some(value => !Number.isSafeInteger(value) || value <= 0 || value > 2147483647)) throw new PlanningApiError('configuration')

  async function transport(path, { session, method = 'GET', body, signal, authenticated = true, requestTimeoutMs = timeoutMs, timeoutKind = 'timeout', validate } = {}) {
    if (signal?.aborted) throw cancelled()
    if (authenticated) session = projectSession(session, now())
    const controller = new AbortController()
    let timer
    let abort
    const interruption = new Promise((_, reject) => {
      abort = () => { controller.abort(); reject(cancelled()) }
      signal?.addEventListener('abort', abort, { once: true })
      timer = setTimeout(() => { controller.abort(); reject(new PlanningApiError(timeoutKind)) }, requestTimeoutMs)
    })
    const work = async () => {
      let response
      try {
        response = await fetchImpl(`${root}${path}`, {
          method,
          headers: { Accept: 'application/json', ...(authenticated ? { Authorization: `Bearer ${session.access_token}` } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
          credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal,
        })
      } catch (error) {
        if (controller.signal.aborted || error?.name === 'AbortError') throw cancelled()
        throw new PlanningApiError('network')
      }
      if (controller.signal.aborted) throw cancelled()
      requireResponse(response && !response.redirected && response.type !== 'opaqueredirect' && (!response.url || response.url === `${root}${path}`))
      // Never consume errors that might contain credentials or another user's data.
      if (!response.ok) throw new PlanningApiError('http', response.status)
      requireResponse(response.status === 200)
      let data
      try {
        requireResponse(/^application\/(?:json|[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get('Content-Type') || ''))
        data = await response.json()
      } catch { throw new PlanningApiError('response') }
      if (controller.signal.aborted) throw cancelled()
      if (authenticated) projectSession(session, now())
      return validate(data)
    }
    try { return await Promise.race([interruption, work()]) } catch (error) {
      if (error instanceof PlanningApiError) throw error
      if (error?.name === 'AbortError') throw cancelled()
      throw new PlanningApiError('response')
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
  }

  return {
    checkPlanningHealth({ signal } = {}) {
      return transport('/api/planning/health', { authenticated: false, signal, validate: data => {
        requireResponse(object(data) && data.status === 'healthy')
        return data
      } })
    },
    async createSchedule(form, { session, signal } = {}) {
      const id = projectId(form?.project_id)
      try {
        return await transport('/api/planning/schedule', { method: 'POST', body: buildPlanningRequest(form), session, signal, requestTimeoutMs: generationTimeoutMs, timeoutKind: 'generation_timeout', validate: data => scheduleResponse(data, id) })
      } catch (error) {
        if (error.kind !== 'generation_timeout' || signal?.aborted) throw error
        // Cancellation does not roll back a submitted backend operation. Reconcile
        // once through the same owner-scoped GET; never resubmit the mutation.
        try {
          return await transport(`/api/planning/schedule/${encodeURIComponent(id)}`, { session, signal, validate: data => scheduleResponse(data, id) })
        } catch (lookupError) {
          if (lookupError.status === 401 || lookupError?.name === 'AbortError') throw lookupError
          throw error
        }
      }
    },
    getSchedule(id, { session, signal } = {}) {
      id = projectId(id)
      return transport(`/api/planning/schedule/${encodeURIComponent(id)}`, { session, signal, validate: data => scheduleResponse(data, id) })
    },
    getAllSchedules({ session, signal } = {}) {
      return transport('/api/planning/schedules', { session, signal, validate: data => {
        requireResponse(Array.isArray(data))
        data.forEach(item => scheduleResponse(item))
        requireResponse(new Set(data.map(item => item.project_id)).size === data.length)
        return data
      } })
    },
    updateSchedule(id, form, { session, signal } = {}) {
      id = projectId(id)
      if (projectId(form?.project_id) !== id) throw new PlanningApiError('input')
      return transport(`/api/planning/schedule/${encodeURIComponent(id)}`, { method: 'PUT', body: buildPlanningRequest(form), session, signal, requestTimeoutMs: generationTimeoutMs, timeoutKind: 'generation_timeout', validate: data => scheduleResponse(data, id) })
    },
    deleteSchedule(id, { session, signal } = {}) {
      id = projectId(id)
      return transport(`/api/planning/schedule/${encodeURIComponent(id)}`, { method: 'DELETE', session, signal, validate: data => {
        requireResponse(object(data) && data.project_id === id && data.deleted === true)
        return data
      } })
    },
    assignProjectOwner(id, username, { session, signal } = {}) {
      id = projectId(id)
      if (typeof username !== 'string' || !/^[a-z0-9][a-z0-9_.-]{2,49}$/.test(username)) throw new PlanningApiError('input')
      const current = projectSession(session, now())
      if (current.role !== 'admin') throw new PlanningApiError('http', 403)
      return transport(`/api/planning/schedule/${encodeURIComponent(id)}/owner`, { method: 'PUT', body: { username }, session: current, signal, validate: data => {
        requireResponse(object(data) && Object.keys(data).length === 3 && data.project_id === id && data.owner_username === username && typeof data.owner_user_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(data.owner_user_id))
        return data
      } })
    },
  }
}

// Browser calls carry only the signed-in account/admin bearer, never a service key.
export const checkPlanningHealth = (...args) => createPlanningApi().checkPlanningHealth(...args)
export const createSchedule = (...args) => createPlanningApi().createSchedule(...args)
export const getSchedule = (...args) => createPlanningApi().getSchedule(...args)
export const getAllSchedules = (...args) => createPlanningApi().getAllSchedules(...args)
export const updateSchedule = (...args) => createPlanningApi().updateSchedule(...args)
export const deleteSchedule = (...args) => createPlanningApi().deleteSchedule(...args)
export const assignProjectOwner = (...args) => createPlanningApi().assignProjectOwner(...args)
