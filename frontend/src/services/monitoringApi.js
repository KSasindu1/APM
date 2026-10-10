import { isCalendarDate, knownNumber, localToday, validateCoordinator } from './monitoringView.js'

export function resolveMonitoringApiUrl({ configuredUrl, origin } = {}) {
  return configuredUrl || origin || 'http://127.0.0.1:8000'
}

export const MONITORING_API_URL = resolveMonitoringApiUrl({
  configuredUrl: import.meta.env?.VITE_API_BASE_URL,
  origin: globalThis.location?.origin,
})

const statusMessages = {
  401: 'Your signed-in session was rejected or has expired. Sign in again to verify Monitoring access.',
  403: 'The server denied this operation for the current identity or project.',
  404: 'The requested project baseline or saved report was not found.',
  409: 'The request conflicts with an existing report. Refresh history before retrying.',
  422: 'The server rejected the report inputs. Check project, baseline dates, task observations, and required recording dates.',
  502: 'A configured source returned an invalid response. No source data has been invented.',
  503: 'Monitoring or a required source is not configured or currently unavailable.',
  504: 'A source request timed out. Try again when the source is responding.',
}

export class MonitoringApiError extends Error {
  constructor(message, status = 0, kind = status > 0 ? 'http' : 'response') {
    super(message)
    this.name = 'MonitoringApiError'
    this.status = status
    this.kind = ['http', 'network', 'timeout', 'response', 'configuration'].includes(kind) ? kind : 'response'
  }
}

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
function valid(condition) {
  if (!condition) throw new MonitoringApiError('Monitoring returned malformed or mismatched data. The response was not used.')
}

const optionalStrings = value => value == null || Array.isArray(value) && value.every(item => typeof item === 'string')
const optionalNumber = value => value == null || knownNumber(value)
const optionalDate = value => value == null || isCalendarDate(value)
const optionalArray = (value, predicate) => value == null || Array.isArray(value) && value.every(predicate)
const nonemptyString = value => typeof value === 'string' && value.trim().length > 0 && value === value.trim() && Array.from(value).every(character => character.codePointAt(0) >= 32 && character.codePointAt(0) !== 127)
const contractIdentifier = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(value)
const approvedFields = (value, fields) => object(value) && Object.keys(value).every(key => fields.includes(key))

function isTimestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]00:00)$/.test(value)) return false
  return isCalendarDate(value.slice(0, 10)) && Number(value.slice(11, 13)) < 24 && Number(value.slice(14, 16)) < 60 && Number(value.slice(17, 19)) < 60 && Number.isFinite(Date.parse(value))
}

function validateActualProgress(actual, projectId, observedOn) {
  valid(approvedFields(actual, ['project_id', 'tasks', 'updated_on']) && actual.project_id === projectId && Array.isArray(actual.tasks) && actual.tasks.length > 0 && actual.tasks.length <= 1000 && optionalDate(actual.updated_on))
  const snapshotDate = actual.updated_on ?? observedOn
  valid(snapshotDate <= observedOn)
  for (const task of actual.tasks) {
    valid(approvedFields(task, ['task_id', 'status', 'progress_percentage', 'actual_start', 'actual_end', 'updated_on']) && contractIdentifier(task.task_id) && ['NOT_STARTED', 'PLANNED', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED'].includes(task.status) && knownNumber(task.progress_percentage) && task.progress_percentage >= 0 && task.progress_percentage <= 100)
    valid(['actual_start', 'actual_end', 'updated_on'].every(key => optionalDate(task[key])))
    // Missing update dates remain missing in the payload; this is only the provider's date bound.
    const taskDate = task.updated_on ?? observedOn
    valid(taskDate <= snapshotDate && (task.actual_start == null || task.actual_start <= taskDate) && (task.actual_end == null || task.actual_end <= taskDate))
    valid(task.actual_start == null || task.actual_end == null || task.actual_start <= task.actual_end)
    if (['NOT_STARTED', 'PLANNED'].includes(task.status)) valid(task.progress_percentage === 0 && task.actual_start == null)
    if (task.status === 'COMPLETED') valid(task.progress_percentage === 100 && isCalendarDate(task.actual_end))
    else valid(task.progress_percentage !== 100 && task.actual_end == null)
  }
  valid(new Set(actual.tasks.map(task => task.task_id)).size === actual.tasks.length)
  return actual
}

function validatePlanningObservation(body, projectId, latestDate) {
  valid((body.planning_message != null) === (body.received_on != null))
  if (body.planning_message != null) {
    const message = body.planning_message
    valid(object(message) && object(message.data) && message.data.project_id === projectId && isCalendarDate(body.received_on) && body.received_on <= latestDate)
    valid(optionalDate(message.data.updated_on) && (message.data.updated_on == null || message.data.updated_on <= body.received_on))
  }
}

export function validateObservation(receipt, projectId, observationId, requestId) {
  valid(nonemptyString(projectId) && object(receipt) && receipt.project_id === projectId && nonemptyString(receipt.observation_id) && contractIdentifier(receipt.request_id) && contractIdentifier(receipt.source) && isCalendarDate(receipt.observed_on) && receipt.observed_on <= localToday() && isTimestamp(receipt.accepted_at) && contractIdentifier(receipt.submitted_by) && typeof receipt.persisted === 'boolean' && Array.isArray(receipt.limitations) && receipt.limitations.every(item => typeof item === 'string'))
  valid(observationId === undefined || nonemptyString(observationId) && receipt.observation_id === observationId)
  valid(requestId === undefined || nonemptyString(requestId) && receipt.request_id === requestId)
  validateActualProgress(receipt.actual_progress, projectId, receipt.observed_on)
  // Project-local observation dates are independent of the UTC acceptance date.
  // Source and extra server metadata are labels, not proof of origin or authorization.
  return receipt
}

export function validateReport(report, projectId, reportId, requestId) {
  valid(object(report) && report.project_id === projectId && typeof report.report_id === 'string' && report.report_id.length > 0 && typeof report.request_id === 'string' && report.request_id.length > 0 && isCalendarDate(report.as_of) && ['ON_TRACK', 'AT_RISK', 'DELAYED'].includes(report.status) && object(report.metrics) && Array.isArray(report.tasks) && Array.isArray(report.warnings) && Array.isArray(report.limitations))
  valid(Object.entries(report.metrics).every(([key, value]) => key === 'actual_progress_is_lower_bound' ? typeof value === 'boolean' : optionalNumber(value)))
  valid(report.tasks.every(task => object(task) && typeof task.task_id === 'string' && typeof task.title === 'string' && ['not_started', 'in_progress', 'blocked', 'completed', 'missing', 'stale'].includes(task.status) && ['is_critical', 'delayed', 'dependency_delayed', 'actual_is_stale', 'planned_deadline_passed'].every(key => task[key] == null || typeof task[key] === 'boolean') && ['actual_progress', 'expected_progress', 'variance', 'last_observed_progress', 'overdue_days', 'completed_late_days', 'start_delay_days'].every(key => optionalNumber(task[key])) && ['planned_start', 'start_date', 'due_date', 'actual_start', 'completed_on', 'actual_end', 'last_updated_on', 'observed_on', 'updated_on'].every(key => optionalDate(task[key]))))
  valid(new Set(report.tasks.map(task => task.task_id)).size === report.tasks.length)
  valid(report.warnings.every(warning => object(warning) && typeof warning.code === 'string' && typeof warning.message === 'string' && typeof warning.severity === 'string' && optionalStrings(warning.task_ids) && optionalStrings(warning.source_refs)))
  valid(report.limitations.every(item => typeof item === 'string') && optionalStrings(report.suggested_actions))
  valid(optionalArray(report.risks, risk => object(risk) && typeof risk.risk_id === 'string' && typeof risk.description === 'string' && typeof risk.level === 'string' && optionalStrings(risk.task_ids) && optionalNumber(risk.confidence) && (risk.confidence == null || risk.confidence >= 0 && risk.confidence <= 1) && (risk.evidence == null || typeof risk.evidence === 'string' || optionalStrings(risk.evidence))))
  valid(optionalArray(report.milestones, milestone => object(milestone) && typeof milestone.milestone_id === 'string' && typeof milestone.title === 'string' && typeof milestone.status === 'string' && optionalNumber(milestone.progress)))
  valid(['summary', 'risk_summary', 'generated_at'].every(key => report[key] == null || typeof report[key] === 'string'))
  valid(['planning_context', 'actual_context'].every(key => report[key] == null || object(report[key])))
  valid(['retrieved_evidence', 'risk_evidence_used', 'risk_knowledge_sources'].every(key => report[key] == null || Array.isArray(report[key])))
  const sourceContexts = [
    report.planning_context, report.planning_context?.data,
    report.planning_context?.source_message, report.planning_context?.source_message?.data,
    report.planning_context?.source_payload, report.planning_context?.source_payload?.data,
    report.actual_context, report.actual_context?.source_payload,
    report.actual_context?.observation_receipt, report.actual_context?.observation_receipt?.actual_progress,
    ...report.tasks, ...(report.risks ?? []), ...(report.milestones ?? []),
    ...(report.retrieved_evidence ?? []), ...(report.risk_evidence_used ?? []), ...(report.risk_knowledge_sources ?? []),
  ]
  valid(sourceContexts.every(context => !object(context) || !Object.hasOwn(context, 'project_id') || context.project_id === projectId))
  valid(!reportId || report.report_id === reportId)
  valid(!requestId || report.request_id === requestId)
  if (report.coverage != null) {
    const coverage = report.coverage
    const enums = { planning_status: ['available'], planning_freshness: ['known', 'unknown', 'stale'], actual_status: ['complete', 'partial', 'missing', 'stale'], risk_status: ['available', 'not_supplied', 'stale', 'unavailable'], milestones_status: ['available', 'not_supplied'], expected_progress_method: ['calendar_estimate', 'supplied_target', 'mixed', 'unavailable'], actual_progress_method: ['unweighted_task_average'] }
    valid(object(coverage) && Object.entries(enums).every(([key, values]) => values.includes(coverage[key])))
    valid(['total_tasks', 'fresh_actual_tasks', 'missing_actual_tasks', 'stale_actual_tasks'].every(key => Number.isInteger(coverage[key]) && coverage[key] >= 0))
    valid(['planning_updated_on', 'planning_observed_on', 'actual_updated_on', 'actual_observed_on', 'risk_observed_on'].every(key => optionalDate(coverage[key])))
  }
  return report
}

export function validateMonitoringSession(data) {
  valid(object(data) && contractIdentifier(data.subject) && Array.isArray(data.project_ids) && data.project_ids.every(contractIdentifier) && Array.isArray(data.permissions) && data.permissions.every(permission => ['monitoring:read', 'monitoring:generate'].includes(permission)))
  valid(new Set(data.project_ids).size === data.project_ids.length && new Set(data.permissions).size === data.permissions.length)
  valid(!Object.hasOwn(data, 'unsupported_project_count') || Number.isSafeInteger(data.unsupported_project_count) && data.unsupported_project_count >= 0)
  return Object.freeze({ subject: data.subject, project_ids: Object.freeze([...data.project_ids]), permissions: Object.freeze([...data.permissions]), ...(Object.hasOwn(data, 'unsupported_project_count') ? { unsupported_project_count: data.unsupported_project_count } : {}) })
}

export function createMonitoringApi({ baseUrl = MONITORING_API_URL, token = '', fetchImpl = globalThis.fetch, timeoutMs = 45000 } = {}) {
  let url
  try { url = new URL(baseUrl) } catch { throw new MonitoringApiError('The configured Monitoring API URL is invalid.', 0, 'configuration') }
  if (!(url.protocol === 'https:' || url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) || url.username || url.password || url.search || url.hash) throw new MonitoringApiError('The configured Monitoring API URL is invalid.', 0, 'configuration')
  const root = url.href.replace(/\/$/, '')
  const pathId = value => {
    valid(typeof value === 'string' && value.length > 0)
    return encodeURIComponent(value)
  }

  async function request(path, { signal, body, publicRequest = false } = {}) {
    if (!publicRequest && (typeof token !== 'string' || !token.trim() || /[\r\n]/.test(token))) throw new MonitoringApiError(statusMessages[401], 401)
    const controller = new AbortController()
    const abort = () => controller.abort(signal?.reason)
    if (signal?.aborted) abort()
    signal?.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => controller.abort(new DOMException('Monitoring request timed out', 'TimeoutError')), timeoutMs)
    let interrupt
    const interruption = new Promise((_, reject) => {
      interrupt = () => reject(controller.signal.reason)
      controller.signal.addEventListener('abort', interrupt, { once: true })
    })
    const work = async () => {
      controller.signal.throwIfAborted()
      const init = {
        method: body ? 'POST' : 'GET',
        headers: { Accept: 'application/json', ...(!publicRequest ? { Authorization: `Bearer ${token.trim()}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
        signal: controller.signal,
        credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
      }
      let response
      try { response = await fetchImpl(`${root}/api/monitoring${path}`, init) } catch (error) {
        if (controller.signal.aborted || error instanceof MonitoringApiError || error?.name === 'AbortError') throw error
        throw new MonitoringApiError('Cannot reach the Monitoring API. Check its configuration, availability, and CORS policy.', 0, 'network')
      }
      controller.signal.throwIfAborted()
      if (!response.ok) {
        // Do not echo server detail/input/HTML: it may contain private source values.
        throw new MonitoringApiError(statusMessages[response.status] || `Monitoring request failed (HTTP ${response.status}).`, response.status, 'http')
      }
      let data
      try { data = await response.json() } catch { throw new MonitoringApiError('Monitoring returned invalid JSON. The response was not used.') }
      controller.signal.throwIfAborted()
      return data
    }
    try {
      // Bound fetch and body parsing even if a transport ignores cancellation.
      return await Promise.race([work(), interruption])
    } catch (error) {
      if (controller.signal.aborted) {
        if (signal?.aborted) throw signal.reason || new DOMException('Request cancelled', 'AbortError')
        throw new MonitoringApiError('Monitoring request timed out. Check the API and configured sources.', 0, 'timeout')
      }
      if (error instanceof MonitoringApiError || error?.name === 'AbortError') throw error
      throw new MonitoringApiError('Monitoring returned malformed or mismatched data. The response was not used.')
    } finally {
      clearTimeout(timer)
      controller.signal.removeEventListener('abort', interrupt)
      signal?.removeEventListener('abort', abort)
    }
  }

  return {
    async health(signal) {
      const data = await request('/health', { signal, publicRequest: true })
      valid(object(data) && ['ready', 'configuration_required'].includes(data.status) && object(data.capabilities) && ['auth_configured', 'planning_configured', 'risk_configured', 'storage_configured'].every(key => typeof data.capabilities[key] === 'boolean') && (!Object.hasOwn(data.capabilities, 'observations_storage_configured') || typeof data.capabilities.observations_storage_configured === 'boolean') && typeof data.live_sources_verified === 'boolean')
      return data
    },
    async session(signal) {
      const data = await request('/session', { signal })
      return validateMonitoringSession(data)
    },
    async baseline(projectId, signal) {
      const data = await request(`/projects/${pathId(projectId)}/baseline`, { signal })
      valid(object(data) && data.project_id === projectId && isCalendarDate(data.fetched_on) && optionalDate(data.updated_on) && Array.isArray(data.tasks) && data.tasks.every(task => object(task) && typeof task.task_id === 'string' && typeof task.title === 'string' && isCalendarDate(task.start_date) && isCalendarDate(task.due_date) && task.due_date >= task.start_date && optionalStrings(task.dependency_ids)))
      valid(optionalStrings(data.limitations) && (data.context == null || object(data.context)))
      valid(new Set(data.tasks.map(task => task.task_id)).size === data.tasks.length)
      return data
    },
    async history(projectId, signal) {
      const data = await request(`/projects/${pathId(projectId)}/reports?limit=20`, { signal })
      valid(Array.isArray(data))
      valid(new Set(data.map(report => report?.report_id)).size === data.length)
      return data.map(report => validateReport(report, projectId))
    },
    async report(projectId, reportId, signal) {
      return validateReport(await request(`/projects/${pathId(projectId)}/reports/${pathId(reportId)}`, { signal }), projectId, reportId)
    },
    async submitObservation(projectId, body, signal) {
      const fields = ['request_id', 'source', 'observed_on', 'actual_progress', 'save_observation', 'planning_message', 'received_on']
      valid(nonemptyString(projectId) && approvedFields(body, fields) && contractIdentifier(body.request_id) && contractIdentifier(body.source) && isCalendarDate(body.observed_on) && body.observed_on <= localToday())
      valid(!Object.hasOwn(body, 'save_observation') || typeof body.save_observation === 'boolean')
      validateActualProgress(body.actual_progress, projectId, body.observed_on)
      validatePlanningObservation(body, projectId, localToday())
      const payload = { ...body, save_observation: body.save_observation ?? false }
      const receipt = validateObservation(await request(`/projects/${pathId(projectId)}/observations`, { signal, body: payload }), projectId, undefined, payload.request_id)
      valid(receipt.persisted === payload.save_observation && receipt.observed_on === payload.observed_on && receipt.source === payload.source)
      return receipt
    },
    async observationHistory(projectId, signal) {
      valid(nonemptyString(projectId))
      const data = await request(`/projects/${pathId(projectId)}/observations?limit=20`, { signal })
      valid(Array.isArray(data) && data.length <= 20)
      valid(new Set(data.map(receipt => receipt?.observation_id)).size === data.length)
      return data.map(receipt => {
        validateObservation(receipt, projectId)
        valid(receipt.persisted === true)
        return receipt
      })
    },
    async observation(projectId, observationId, signal) {
      valid(nonemptyString(projectId) && nonemptyString(observationId))
      const receipt = validateObservation(await request(`/projects/${pathId(projectId)}/observations/${pathId(observationId)}`, { signal }), projectId, observationId)
      valid(receipt.persisted === true)
      return receipt
    },
    async generate(body, signal) {
      const fields = ['project_id', 'request_id', 'as_of', 'planning_message', 'received_on', 'actual_progress', 'observed_on', 'observation_id', 'include_risks', 'save_report', 'query']
      valid(approvedFields(body, fields) && nonemptyString(body.project_id) && contractIdentifier(body.request_id) && isCalendarDate(body.as_of) && body.as_of <= localToday())
      valid(['include_risks', 'save_report'].every(key => !Object.hasOwn(body, key) || typeof body[key] === 'boolean'))
      validatePlanningObservation(body, body.project_id, body.as_of)
      if (Object.hasOwn(body, 'observation_id')) {
        valid(nonemptyString(body.observation_id) && !Object.hasOwn(body, 'actual_progress') && !Object.hasOwn(body, 'observed_on'))
      } else {
        valid((body.actual_progress != null) === (body.observed_on != null))
        if (body.actual_progress != null) {
          valid(isCalendarDate(body.observed_on) && body.observed_on <= body.as_of)
          validateActualProgress(body.actual_progress, body.project_id, body.observed_on)
        }
      }
      const report = validateReport(await request('/reports', { signal, body }), body.project_id, undefined, body.request_id)
      valid(report.as_of === body.as_of)
      return report
    },
    async coordinator(projectId, reportId, signal) {
      const data = await request(`/projects/${pathId(projectId)}/reports/${pathId(reportId)}/coordinator-message`, { signal })
      validateReport(data?.report, projectId, reportId)
      try { return validateCoordinator(data, projectId, reportId) } catch { throw new MonitoringApiError('Coordinator message metadata did not match the saved report.') }
    },
  }
}
