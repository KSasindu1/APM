const monitoringFailureKinds = new Set(['http', 'network', 'timeout', 'response', 'configuration'])

// Keep only bounded classification metadata, never the error or its private details.
export function monitoringFailureMetadata(error) {
  const rawStatus = error?.status
  const status = Number.isInteger(rawStatus) && rawStatus >= 100 && rawStatus <= 599 ? rawStatus : 0
  const kind = monitoringFailureKinds.has(error?.kind) ? error.kind : status ? 'http' : null
  return { status, kind }
}

export function monitoringFailureGuidance(failure) {
  if (!failure) return null
  const { status, kind } = monitoringFailureMetadata(failure)
  if (status === 401) return {
    title: 'Monitoring access rejected (HTTP 401)',
    message: 'The backend rejected your signed-in session. Sign in again to verify Monitoring access. The previous project inputs and report are cleared; retrying cannot renew an expired session.',
    showConnectionHelp: false,
  }
  if (status === 403) return {
    title: 'Monitoring access denied (HTTP 403)',
    message: 'The backend responded, but your signed-in account is not authorized for this project or operation. Refresh project access and check ownership and required Monitoring permissions with an administrator. This does not sign out your account or grant new permissions.',
    showConnectionHelp: false,
  }
  if (status === 404) return {
    title: 'Monitoring project or artifact unavailable (HTTP 404)',
    message: 'This project, baseline, report, or receipt is unavailable or no longer accessible to your signed-in account. Refresh project access before retrying. The backend returned an HTTP response; this does not sign out your account.',
    showConnectionHelp: false,
  }
  if (status === 503) return {
    title: 'Monitoring service configuration required (HTTP 503)',
    message: 'The backend responded, but Monitoring or a required source is not configured or currently unavailable. Ask the operator to check signed-in authentication and required source configuration, then refresh project access / Check API. Signing in again cannot configure the service.',
    showConnectionHelp: false,
  }
  // An HTTP response must not be presented as a browser/network failure.
  if (status >= 400 || status > 0 && kind !== 'response' && kind !== 'configuration') return {
    title: `Monitoring API responded with HTTP ${status}`,
    message: 'Review the reported status, requested inputs, and operator backend logs before retrying. The backend returned an HTTP response; this is not evidence of a network failure.',
    showConnectionHelp: false,
  }
  if (kind === 'response') return {
    title: 'Monitoring response could not be verified',
    message: 'The response was malformed or did not match the requested project or report. No response data was used. Ask the operator to check frontend/backend API compatibility and response metadata before retrying.',
    showConnectionHelp: false,
  }
  if (kind === 'configuration') return {
    title: 'Monitoring API configuration needs attention',
    message: 'Ask the operator to verify the configured API URL and that the frontend/backend settings agree, then use Check API after configuration changes. Repeated sign-in attempts will not fix this configuration.',
    showConnectionHelp: false,
  }
  if (kind === 'network') return {
    title: 'Cannot reach the Monitoring API',
    message: 'No usable HTTP response was received. Check backend availability and the browser connection, API address, proxy, and CORS settings before retrying.',
    showConnectionHelp: true,
  }
  if (kind === 'timeout') return {
    title: 'Monitoring request timed out',
    message: 'No response was verified before the request deadline. Check the API and required sources before retrying. A timed-out save may still finish on the server; check report history before resubmitting it.',
    showConnectionHelp: true,
  }
  return {
    title: 'Monitoring operation could not be completed',
    message: 'No verified result is available. Use Check API and ask the operator to inspect backend logs. Do not share tokens or Authorization headers when reporting an error.',
    showConnectionHelp: false,
  }
}

export function monitoringAccessReadiness(healthSnapshot) {
  const authMissing = healthSnapshot?.capabilities?.auth_configured === false
  return {
    authMissing,
    // Public health is diagnostic, not an account grant. Older servers may
    // report opaque-token configuration separately from signed-in bearer auth.
    canVerify: true,
    message: authMissing ? 'The server reports authentication configuration is missing. Signed-in access must still be verified by the protected Monitoring session endpoint; no project access is inferred from health. Ask the operator to check signed-in authentication support, then refresh project access / Check API.' : '',
  }
}

// Calendar dates are project-local dates, not UTC instants.
export function localToday(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

export function isCalendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(0)
  date.setFullYear(year, month - 1, day)
  date.setHours(12, 0, 0, 0)
  return year >= 1 && date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
}

export function formatDate(value) {
  if (!isCalendarDate(value)) return 'Unknown'
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(0)
  date.setFullYear(year, month - 1, day)
  date.setHours(12, 0, 0, 0)
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export const knownNumber = value => typeof value === 'number' && Number.isFinite(value)
export const formatNumber = value => knownNumber(value) ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value) : 'Unknown'
export const formatPercent = value => knownNumber(value) ? `${formatNumber(value)}%` : 'Unknown'
export const formatVariance = value => knownNumber(value) ? `${value > 0 ? '+' : ''}${formatNumber(value)} pp` : 'Unknown'
export const displayProgress = value => knownNumber(value) ? Math.min(100, Math.max(0, value)) : null
export const readable = value => typeof value === 'string' ? value.replaceAll('_', ' ') : 'Unknown'
export const plainText = value => typeof value === 'string' ? value : value == null ? 'Unknown' : JSON.stringify(value, null, 2)

export function assessmentDates(task) {
  return {
    plannedStart: task.planned_start ?? task.start_date ?? null,
    dueDate: task.due_date ?? null,
    actualStart: task.actual_start ?? null,
    actualEnd: task.completed_on ?? task.actual_end ?? null,
    observedOn: task.last_updated_on ?? task.observed_on ?? task.updated_on ?? null,
  }
}

export function filterTasks(tasks, search = '', status = 'all') {
  const term = search.trim().toLowerCase()
  return tasks.filter(task => {
    const matchesText = `${task.title} ${task.task_id}`.toLowerCase().includes(term)
    const normalized = String(task.status).toLowerCase()
    const matchesStatus = status === 'all' || (status === 'delayed' ? task.delayed === true : status === 'dependency' ? task.dependency_delayed === true : status === 'stale' ? normalized === 'stale' || task.actual_is_stale === true : normalized === status)
    return matchesText && matchesStatus
  })
}

export function reportScope(report) {
  return report.coverage == null ? 'Unknown legacy scope — coverage was not recorded' : 'Based on available inputs'
}

export function highRiskCount(report) {
  if (report.coverage?.risk_status !== 'available') return null
  return knownNumber(report.metrics?.high_risks) ? report.metrics.high_risks : null
}

export function blankActualRows(tasks) {
  return tasks.map(task => ({ task_id: task.task_id, included: false, status: '', progress_percentage: '', actual_start: '', actual_end: '' }))
}

function requireValue(condition, message) {
  if (!condition) throw new Error(message)
}

const identifier = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(value)
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)

export function importedPlanning(text, projectId, receivedOn) {
  let message
  try { message = JSON.parse(text) } catch { throw new Error('Planning message must be valid JSON.') }
  requireValue(record(message) && message.sender === 'planning_agent' && message.receiver === 'project_monitor_report_agent' && message.action === 'SCHEDULE_CREATED' && identifier(message.request_id) && (message.status === undefined || message.status === 'success'), 'Use a real, successful SCHEDULE_CREATED Planning envelope.')
  requireValue(record(message.data) && message.data.project_id === projectId && typeof message.data.project_name === 'string' && message.data.project_name.trim() && record(message.data.analysis) && Array.isArray(message.data.schedule), 'Planning data must belong to the selected project and contain its real schedule and analysis.')
  const criticalPath = Object.hasOwn(message.data.analysis, 'critical_path') ? message.data.analysis.critical_path : []
  requireValue(Array.isArray(criticalPath) && criticalPath.every(identifier) && new Set(criticalPath).size === criticalPath.length, 'Planning critical path must contain unique task IDs.')
  const seen = new Set()
  const tasks = message.data.schedule.map(task => {
    requireValue(record(task) && identifier(task.task_id) && !seen.has(task.task_id) && typeof task.name === 'string' && task.name.trim(), 'Planning tasks need unique IDs and titles.')
    seen.add(task.task_id)
    requireValue(isCalendarDate(task.planned_start) && isCalendarDate(task.planned_end) && task.planned_end >= task.planned_start, 'Planning task dates are invalid.')
    requireValue(['low', 'medium', 'high', 'critical'].includes(String(task.priority).toLowerCase()) && Array.isArray(task.dependencies), 'Planning tasks need priority and dependency fields.')
    requireValue(!Object.hasOwn(task, 'is_critical') || typeof task.is_critical === 'boolean', 'Planning critical flags must be booleans.')
    return { task_id: task.task_id, title: task.name, start_date: task.planned_start, due_date: task.planned_end, priority: task.priority, dependency_ids: task.dependencies, is_critical: Object.hasOwn(task, 'is_critical') ? task.is_critical : criticalPath.includes(task.task_id) }
  })
  requireValue(tasks.every(task => task.dependency_ids.every(id => seen.has(id) && id !== task.task_id)), 'Planning contains unknown or self-referencing dependencies.')
  requireValue(criticalPath.every(id => seen.has(id)), 'Planning critical path references unknown tasks.')
  requireValue(isCalendarDate(receivedOn), 'Enter the date the Planning message was received.')
  return { message, baseline: { project_id: projectId, fetched_on: receivedOn, updated_on: message.data.updated_on ?? null, tasks, context: message.data, limitations: ['Imported Planning statuses are not execution observations.', 'The server validates this Planning envelope and its dependencies.'] } }
}

export function buildActualProgress(rows, projectId, observedOn, asOf, today = localToday()) {
  const selected = rows.filter(row => row.included === true)
  if (!selected.length) return undefined
  requireValue(isCalendarDate(observedOn) && observedOn <= asOf && observedOn <= today, 'Actual observations need a recording date no later than the report date or today.')
  const seen = new Set()
  const tasks = selected.map(row => {
    requireValue(identifier(row.task_id) && !seen.has(row.task_id), 'Actual observations need unique, valid task IDs.')
    seen.add(row.task_id)
    requireValue(['NOT_STARTED', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED'].includes(row.status), 'Choose an explicit actual status for every included task.')
    const raw = row.progress_percentage
    requireValue((typeof raw === 'string' && raw.trim() !== '' || typeof raw === 'number') && knownNumber(Number(raw)) && Number(raw) >= 0 && Number(raw) <= 100, 'Enter explicit progress between 0 and 100 for every included task; blank is not zero.')
    const progress = Number(raw)
    requireValue(row.status !== 'NOT_STARTED' || progress === 0, 'Not-started tasks must have zero progress.')
    requireValue(row.status !== 'COMPLETED' || progress === 100 && isCalendarDate(row.actual_end), 'Completed tasks require 100% progress and an actual end date.')
    requireValue(row.status === 'COMPLETED' || progress !== 100 && !row.actual_end, 'Only completed tasks may have 100% progress or an actual end date.')
    for (const field of ['actual_start', 'actual_end']) {
      if (row[field]) requireValue(isCalendarDate(row[field]) && row[field] <= observedOn && row[field] <= asOf && row[field] <= today, 'Actual start/end dates cannot be after their recording date, the report date, or today.')
    }
    requireValue(!row.actual_start || row.status !== 'NOT_STARTED', 'Not-started tasks cannot have an actual start date.')
    requireValue(!row.actual_start || !row.actual_end || row.actual_start <= row.actual_end, 'Actual start cannot be after actual end.')
    return { task_id: row.task_id, status: row.status, progress_percentage: progress, ...(row.actual_start ? { actual_start: row.actual_start } : {}), ...(row.actual_end ? { actual_end: row.actual_end } : {}) }
  })
  return { project_id: projectId, tasks }
}

export function buildMonitoringRequest({ projectId, requestId, asOf, baseline, planningText = '', receivedOn = '', rows = [], observedOn = '', includeRisks = true, saveReport = false, storageConfigured = false, query = '', today = localToday() }) {
  requireValue(identifier(projectId) && identifier(requestId), 'Select an authorized project and create a valid request ID.')
  requireValue(isCalendarDate(asOf) && asOf <= today, 'Choose a valid report date no later than today.')
  let planning
  if (planningText.trim()) {
    planning = importedPlanning(planningText, projectId, receivedOn)
    requireValue(receivedOn <= asOf && receivedOn <= today, 'Planning receipt date cannot be after the report date or today.')
    baseline = planning.baseline
  }
  requireValue(baseline?.project_id === projectId && isCalendarDate(baseline.fetched_on), 'Load a dated Planning baseline or import a real dated Planning message first.')
  requireValue(asOf >= baseline.fetched_on, 'This baseline was fetched after the selected report date. Use a dated historical Planning message instead.')
  requireValue(typeof includeRisks === 'boolean' && typeof saveReport === 'boolean', 'Risk and save options must be explicit booleans.')
  requireValue(!saveReport || storageConfigured, 'Saving is unavailable until report storage is configured.')
  const actual = buildActualProgress(rows, projectId, observedOn, asOf, today)
  requireValue(!actual || actual.tasks.every(task => baseline.tasks.some(planned => planned.task_id === task.task_id)), 'Actual observations must reference tasks in the selected baseline.')
  requireValue(typeof query === 'string' && query.trim().length <= 500, 'The optional query must be at most 500 characters.')
  return { project_id: projectId, request_id: requestId, as_of: asOf, include_risks: includeRisks, save_report: saveReport, ...(planning ? { planning_message: planning.message, received_on: receivedOn } : {}), ...(actual ? { actual_progress: actual, observed_on: observedOn } : {}), ...(query.trim() ? { query: query.trim() } : {}) }
}

const reportFields = ['schema_version', 'report_id', 'request_id', 'project_id', 'requested_by', 'generated_at', 'as_of', 'status', 'metrics', 'coverage', 'tasks', 'milestones', 'warnings', 'retrieved_evidence', 'facts', 'summary_fact_ids', 'summary', 'summary_method', 'limitations', 'suggested_actions', 'risks', 'risk_summary', 'risk_evidence_used', 'risk_knowledge_sources', 'planning_context', 'actual_context', 'planning_feedback', 'human_review_required']
const sensitiveFields = ['principal', 'permissions', 'credentials', 'credential', 'authorization', 'authentication', 'auth', 'password', 'passwd', 'pwd', 'secret', 'secrets', 'token', 'tokens', 'key', 'keys', 'bearer', 'cookie', 'cookies', 'set_cookie', 'auth_header', 'authorization_header', 'api_key', 'api_keys', 'access_key', 'access_key_id', 'private_key', 'secret_key', 'service_key', 'client_secret', 'access_token', 'refresh_token', 'id_token']
const compactSensitiveFields = new Set(sensitiveFields.map(field => field.replaceAll('_', '')))
function sensitiveKey(key) {
  const normalized = key.replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2').replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return compactSensitiveFields.has(normalized.replaceAll('_', '')) || sensitiveFields.some(field => normalized.endsWith(`_${field}`))
}
function exportSafe(value) {
  if (Array.isArray(value)) return value.map(exportSafe)
  if (record(value)) return Object.fromEntries(Object.entries(value).filter(([key]) => !sensitiveKey(key)).map(([key, item]) => [key, exportSafe(item)]))
  // Source envelopes may carry JSON-encoded metadata inside an ordinary string.
  if (typeof value === 'string' && ['{', '[', '"'].includes(value.trimStart()[0])) {
    try {
      const decoded = JSON.parse(value)
      const safe = exportSafe(decoded)
      if (JSON.stringify(safe) !== JSON.stringify(decoded)) return JSON.stringify(safe)
    } catch { /* Plain source text remains evidence, not a JSON validation failure. */ }
  }
  return value
}

export function reportExport(report, projectId) {
  requireValue(record(report) && report.project_id === projectId && typeof report.report_id === 'string' && report.report_id && typeof report.request_id === 'string' && report.request_id && isCalendarDate(report.as_of) && record(report.metrics) && Array.isArray(report.tasks), 'Report export metadata does not match the selected project.')
  return exportSafe(Object.fromEntries(reportFields.filter(field => Object.hasOwn(report, field)).map(field => [field, report[field]])))
}

export function feedbackExport(report, projectId) {
  const safe = reportExport(report, projectId)
  requireValue(Array.isArray(safe.planning_feedback), 'The report has no valid Planning feedback collection.')
  requireValue(safe.planning_feedback.every(message => record(message) && message.sender === 'project_monitor_report_agent' && message.receiver === 'planning_agent' && message.action === 'SCHEDULE_DELAY_DETECTED' && message.request_id === report.request_id && record(message.data) && message.data.project_id === projectId), 'Planning feedback metadata does not match the report.')
  return { project_id: projectId, report_id: report.report_id, request_id: report.request_id, planning_feedback: safe.planning_feedback }
}

export function coordinatorExport(report, projectId) {
  const safe = reportExport(report, projectId)
  return { message_type: 'monitoring.report.ready', sender: 'monitoring_reporting', recipient: 'coordinator', correlation_id: safe.request_id, project_id: projectId, report: safe }
}

export function validateCoordinator(message, projectId, reportId) {
  requireValue(record(message) && message.message_type === 'monitoring.report.ready' && message.sender === 'monitoring_reporting' && message.recipient === 'coordinator' && message.project_id === projectId && message.report?.report_id === reportId && message.correlation_id === message.report?.request_id, 'Coordinator message metadata does not match the selected report.')
  return coordinatorExport(message.report, projectId)
}

const expectedMethods = new Set(['calendar_estimate', 'supplied_target', 'mixed'])
const executionStatuses = new Set(['not_started', 'in_progress', 'blocked', 'completed'])
const textOrder = (a, b) => a < b ? -1 : a > b ? 1 : 0
const statusOf = task => typeof task.status === 'string' ? task.status.toLowerCase() : null
const percentNumber = value => knownNumber(value) && value >= 0 && value <= 100
const countNumber = value => Number.isInteger(value) && value >= 0
const dateOrNull = value => isCalendarDate(value) ? value : null
const stringOrNull = value => typeof value === 'string' && value.trim() ? value : null
const uniqueStrings = values => [...new Set(values)].sort(textOrder)

// Date.parse alone accepts rolled dates and timezone-less local timestamps.
function verifiedTimestamp(value) {
  if (typeof value !== 'string') return null
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (!match || !isCalendarDate(match[1]) || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return null
  if (match[6] !== 'Z') {
    const [hours, minutes] = match[6].slice(1).split(':').map(Number)
    if (hours > 14 || minutes > 59 || hours === 14 && minutes !== 0) return null
  }
  // Parse whole seconds only; Date.parse would truncate backend microseconds.
  const time = Date.parse(`${match[1]}T${match[2]}:${match[3]}:${match[4]}${match[6]}`)
  return Number.isFinite(time) ? BigInt(time) * 1000000n + BigInt((match[5] ?? '').padEnd(9, '0')) : null
}

function safeMonitoringReport(report, projectId) {
  requireValue(identifier(projectId), 'Select a valid project before using monitoring reports.')
  const safe = reportExport(report, projectId)
  requireValue(identifier(safe.report_id) && identifier(safe.request_id), 'Report metadata needs valid report and request IDs.')
  const evidence = [safe.retrieved_evidence, safe.risk_evidence_used, safe.risk_knowledge_sources].flatMap(items => Array.isArray(items) ? items : [])
  const contexts = [safe.planning_context, safe.planning_context?.data, safe.planning_context?.source_message, safe.planning_context?.source_message?.data, safe.planning_context?.source_payload, safe.actual_context, safe.actual_context?.source_payload, ...safe.tasks, ...(Array.isArray(safe.risks) ? safe.risks : []), ...evidence]
  requireValue(contexts.every(context => !record(context) || !Object.hasOwn(context, 'project_id') || context.project_id === projectId), 'Report source metadata does not match the selected project.')
  const ids = safe.tasks.map(task => record(task) ? task.task_id : null)
  requireValue(ids.every(identifier) && new Set(ids).size === ids.length, 'Report task metadata needs unique, valid task IDs.')
  return safe
}

function freshTask(report, task) {
  const coverage = report.coverage
  const dates = assessmentDates(task)
  const status = statusOf(task)
  const cutoff = dates.observedOn ?? report.as_of
  const validDates = [dates.actualStart, dates.actualEnd].every(date => date == null || isCalendarDate(date) && date <= cutoff && date <= report.as_of)
    && (dates.actualStart == null || dates.actualEnd == null || dates.actualStart <= dates.actualEnd)
  const consistent = status === 'completed' ? task.actual_progress === 100 && isCalendarDate(dates.actualEnd)
    : task.actual_progress < 100 && dates.actualEnd == null && (status !== 'not_started' || task.actual_progress === 0 && dates.actualStart == null)
  return record(coverage) && ['complete', 'partial'].includes(coverage.actual_status)
    && executionStatuses.has(status) && task.actual_is_stale !== true
    && percentNumber(task.actual_progress) && validDates && consistent
    && (dates.observedOn == null || isCalendarDate(dates.observedOn) && dates.observedOn <= report.as_of)
}

function riskAvailable(report) {
  const observed = report.coverage?.risk_observed_on
  return report.coverage?.risk_status === 'available'
    && (observed == null || isCalendarDate(observed) && observed <= report.as_of)
}

function riskCollection(report) {
  if (!riskAvailable(report) || !Array.isArray(report.risks)) return null
  const ids = report.risks.map(risk => record(risk) ? risk.risk_id : null)
  if (!ids.every(identifier) || new Set(ids).size !== ids.length
    || report.risks.some(risk => Object.hasOwn(risk, 'project_id') && risk.project_id !== report.project_id)) return null
  return new Map(report.risks.map(risk => [risk.risk_id, risk]))
}

function metricState(report) {
  const coverage = report.coverage
  const total = report.tasks.length
  const complete = record(coverage) && coverage.actual_status === 'complete' && total > 0
    && coverage.total_tasks === total && coverage.fresh_actual_tasks === total
    && coverage.missing_actual_tasks === 0 && coverage.stale_actual_tasks === 0
    && report.metrics.actual_progress_is_lower_bound !== true
    && report.tasks.every(task => freshTask(report, task))
  const actualMethod = coverage?.actual_progress_method === 'unweighted_task_average'
  const expectedMethod = expectedMethods.has(coverage?.expected_progress_method)
  const planned = coverage?.planning_status === 'available' && expectedMethod && total > 0
    && report.tasks.every(task => percentNumber(task.expected_progress))
  const actual = complete && actualMethod && percentNumber(report.metrics.actual_progress) ? report.metrics.actual_progress : null
  const expected = planned && percentNumber(report.metrics.expected_progress) ? report.metrics.expected_progress : null
  const count = field => complete && countNumber(report.metrics[field]) && report.metrics[field] <= total ? report.metrics[field] : null
  return {
    complete, actualMethod, expectedMethod,
    actual_progress: actual,
    expected_progress: expected,
    progress_variance: actual !== null && expected !== null && knownNumber(report.metrics.progress_variance) ? report.metrics.progress_variance : null,
    delayed_tasks: expectedMethod ? count('delayed_tasks') : null,
    blocked_tasks: count('blocked_tasks'),
    overdue_tasks: count('overdue_tasks'),
    high_risks: riskAvailable(report) && countNumber(report.metrics.high_risks) ? report.metrics.high_risks : null,
  }
}

function dependenciesFor(report, task) {
  const context = report.planning_context
  const collections = [context?.tasks, context?.schedule, context?.data?.schedule, context?.source_message?.data?.schedule]
  const source = [task, ...collections.flatMap(items => Array.isArray(items) ? items.filter(item => record(item) && item.task_id === task.task_id) : [])]
    .find(item => Object.hasOwn(item, 'dependency_ids') || Object.hasOwn(item, 'dependencies'))
  if (!source) return null
  const ids = source.dependency_ids ?? source.dependencies
  if (!Array.isArray(ids) || !ids.every(id => identifier(id) && id !== task.task_id && report.tasks.some(item => item.task_id === id)) || new Set(ids).size !== ids.length) return null
  return uniqueStrings(ids)
}

function targetBaseline(report, task) {
  const method = report.coverage?.expected_progress_method
  if (method === 'calendar_estimate') return { method, target: null }
  if (method === 'supplied_target') return percentNumber(task.expected_progress) ? { method, target: task.expected_progress } : null
  if (method !== 'mixed') return null
  const context = report.planning_context
  const collections = [context?.tasks, context?.schedule, context?.data?.tasks, context?.data?.schedule, context?.source_message?.data?.tasks, context?.source_message?.data?.schedule]
  const sources = collections.flatMap(items => Array.isArray(items) ? items.filter(item => record(item) && item.task_id === task.task_id) : [])
  // An assessment's expected_progress is computed even for calendar tasks, so it cannot establish mixed provenance.
  if (!sources.length || sources.some(source => !Object.hasOwn(source, 'expected_progress')
    || Object.hasOwn(source, 'project_id') && source.project_id !== report.project_id
    || source.expected_progress !== null && (!percentNumber(source.expected_progress) || source.expected_progress !== task.expected_progress)
    || source.expected_progress !== sources[0].expected_progress)) return null
  const target = sources[0].expected_progress
  return { method: target === null ? 'calendar_estimate' : 'supplied_target', target }
}

function taskTargetsSame(current, previous, task, old) {
  if (!methodsMatch(current, previous, 'expected_progress_method')) return null
  const a = targetBaseline(current, task)
  const b = targetBaseline(previous, old)
  return a === null || b === null ? null : a.method === b.method && a.target === b.target
}

function taskBaseline(report, task) {
  const dates = assessmentDates(task)
  const validOrder = isCalendarDate(dates.plannedStart) && isCalendarDate(dates.dueDate) && dates.plannedStart <= dates.dueDate
  return {
    planned_start: validOrder ? dates.plannedStart : null, due_date: validOrder ? dates.dueDate : null,
    is_critical: typeof task.is_critical === 'boolean' ? task.is_critical : null,
    dependencies: dependenciesFor(report, task),
  }
}

function taskBaselineChanged(current, previous, task, old) {
  const a = taskBaseline(current, task)
  const b = taskBaseline(previous, old)
  return ['planned_start', 'due_date', 'is_critical'].some(field => a[field] !== null && b[field] !== null && a[field] !== b[field])
    || a.dependencies !== null && b.dependencies !== null && JSON.stringify(a.dependencies) !== JSON.stringify(b.dependencies)
    || taskTargetsSame(current, previous, task, old) === false
}

function baselineComparison(current, previous) {
  const before = new Map(previous.tasks.map(task => [task.task_id, task]))
  let changed = current.tasks.length !== before.size || current.tasks.some(task => !before.has(task.task_id))
  let unavailable = false
  let dependenciesUnavailable = false
  let targetsSame = methodsMatch(current, previous, 'expected_progress_method') ? true : null
  let targetsUnavailable = false
  for (const task of current.tasks) {
    const old = before.get(task.task_id)
    if (!old) continue
    const a = taskBaseline(current, task)
    const b = taskBaseline(previous, old)
    for (const field of ['planned_start', 'due_date', 'is_critical']) {
      if (a[field] === null || b[field] === null) unavailable = true
      else if (a[field] !== b[field]) changed = true
    }
    if (a.dependencies === null || b.dependencies === null) dependenciesUnavailable = true
    else if (JSON.stringify(a.dependencies) !== JSON.stringify(b.dependencies)) changed = true
    const targetSame = taskTargetsSame(current, previous, task, old)
    if (targetSame === false) {
      changed = true
      targetsSame = false
    } else if (targetSame === null) {
      targetsUnavailable = true
      if (targetsSame !== false) targetsSame = null
    }
  }
  const reasons = []
  if (changed) reasons.push('Planning baseline changed (task IDs, planned dates, criticality, recorded dependencies or supplied targets); added/removed tasks are not completed work.')
  if (unavailable) reasons.push('Planning baseline metadata is unavailable; a matching baseline cannot be verified.')
  if (dependenciesUnavailable) reasons.push('Dependency metadata unavailable; baseline comparison uses task IDs, planned dates and criticality only.')
  if (targetsUnavailable && methodsMatch(current, previous, 'expected_progress_method')) reasons.push('Expected target provenance metadata is unavailable; expected progress, variance and delayed-task comparison eligibility is unknown.')
  return { same: !changed && !unavailable, targetsSame, changed, reasons }
}

function methodsMatch(current, previous, field) {
  const a = current.coverage?.[field]
  const b = previous.coverage?.[field]
  const supported = field === 'actual_progress_method' ? a === 'unweighted_task_average' : expectedMethods.has(a)
  return supported && a === b
}

const deltaFields = ['actual_progress', 'expected_progress', 'progress_variance', 'delayed_tasks', 'blocked_tasks', 'overdue_tasks', 'high_risks']
const emptyRiskChanges = () => ({ new: [], no_longer_returned: [], status_changed: [], assessment_available: false })

/** Comparable means at least one metric or risk assessment is usable, not that every delta is known. */
export function compareMonitoringReports(current, previous, projectId) {
  const now = safeMonitoringReport(current, projectId)
  const result = {
    current_report_id: now.report_id, previous_report_id: null, comparable: false, reasons: [],
    deltas: Object.fromEntries(deltaFields.map(field => [field, null])), task_changes: [], risk_changes: emptyRiskChanges(),
  }
  if (previous == null) {
    result.reasons.push('No previous saved report was supplied.')
    return result
  }
  const before = safeMonitoringReport(previous, projectId)
  result.previous_report_id = before.report_id
  const nowTime = verifiedTimestamp(now.generated_at)
  const beforeTime = verifiedTimestamp(before.generated_at)
  if (now.report_id === before.report_id) result.reasons.push('The same report ID cannot be compared with itself.')
  if (nowTime === null || beforeTime === null) result.reasons.push('Verified generated_at metadata is unavailable; snapshot chronology cannot be established.')
  else if (beforeTime >= nowTime) result.reasons.push('The previous report must have an earlier generated_at timestamp.')
  if (before.as_of > now.as_of) result.reasons.push('The previous report has a future as_of date relative to the current report.')
  if (result.reasons.length) return result

  const a = metricState(now)
  const b = metricState(before)
  const baseline = baselineComparison(now, before)
  result.reasons.push(...baseline.reasons)
  if (!record(now.coverage) || !record(before.coverage)) result.reasons.push('Unknown legacy coverage; unrecorded coverage is not zero or proof of complete execution.')
  if (!a.complete || !b.complete) result.reasons.push('Actual coverage is not fresh and complete in both reports; aggregate actual progress and execution-count deltas are unavailable.')
  const sameActualMethod = methodsMatch(now, before, 'actual_progress_method')
  const sameExpectedMethod = methodsMatch(now, before, 'expected_progress_method')
  if (!sameActualMethod) result.reasons.push('Actual progress method changed or is unavailable; actual progress deltas are unavailable.')
  if (!sameExpectedMethod) result.reasons.push('Expected progress method changed or is unavailable; expected progress, variance and delayed-task deltas are unavailable.')
  const allowed = {
    actual_progress: baseline.same && sameActualMethod,
    expected_progress: baseline.same && baseline.targetsSame === true && sameExpectedMethod,
    progress_variance: baseline.same && baseline.targetsSame === true && sameActualMethod && sameExpectedMethod,
    delayed_tasks: baseline.same && baseline.targetsSame === true && sameActualMethod && sameExpectedMethod,
    blocked_tasks: baseline.same,
    overdue_tasks: baseline.same,
    high_risks: riskAvailable(now) && riskAvailable(before),
  }
  for (const field of deltaFields) {
    if (allowed[field] && a[field] !== null && b[field] !== null) result.deltas[field] = Number((a[field] - b[field]).toFixed(10))
    else result.reasons.push(`${readable(field)} delta unavailable for these snapshots.`)
  }

  const oldTasks = new Map(before.tasks.map(task => [task.task_id, task]))
  const newTasks = new Map(now.tasks.map(task => [task.task_id, task]))
  for (const id of uniqueStrings([...oldTasks.keys(), ...newTasks.keys()])) {
    const old = oldTasks.get(id)
    const task = newTasks.get(id)
    const progressDelta = old && task && allowed.actual_progress && a.complete && b.complete
      ? Number((task.actual_progress - old.actual_progress).toFixed(10)) : null
    let change = null
    if (!old) change = 'added_to_baseline'
    else if (!task) change = 'removed_from_baseline'
    else if (taskBaselineChanged(now, before, task, old)) change = 'baseline_changed'
    else if (statusOf(task) !== statusOf(old)) change = freshTask(now, task) && freshTask(before, old) ? 'status_changed' : 'observation_changed'
    else if (progressDelta !== null && progressDelta !== 0) change = 'progress_changed'
    if (change) result.task_changes.push({
      task_id: id, title: stringOrNull((task ?? old).title) ?? id,
      from_status: old ? stringOrNull(old.status) : null, to_status: task ? stringOrNull(task.status) : null,
      progress_delta: progressDelta, change,
    })
  }

  const oldRisks = riskCollection(before)
  const newRisks = riskCollection(now)
  if (oldRisks && newRisks) {
    result.risk_changes.assessment_available = true
    for (const id of uniqueStrings([...oldRisks.keys(), ...newRisks.keys()])) {
      const old = oldRisks.get(id)
      const risk = newRisks.get(id)
      if (!old) result.risk_changes.new.push(risk)
      else if (!risk) result.risk_changes.no_longer_returned.push(old)
      else if (stringOrNull(old.status) && stringOrNull(risk.status) && old.status !== risk.status) result.risk_changes.status_changed.push({ risk_id: id, description: stringOrNull(risk.description) ?? '', from_status: old.status, to_status: risk.status })
    }
    if (result.risk_changes.no_longer_returned.length) result.reasons.push('Risks no longer returned are not resolved; only explicit closed/mitigated status transitions establish that change.')
  } else result.reasons.push('Fresh risk assessments are not available in both reports; risk differences are unknown.')
  result.comparable = deltaFields.some(field => result.deltas[field] !== null) || result.risk_changes.assessment_available
  result.reasons = uniqueStrings(result.reasons)
  return result
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (record(value)) return `{${Object.keys(value).sort(textOrder).map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}

function historyReports(reports, projectId, limitations = []) {
  requireValue(identifier(projectId) && Array.isArray(reports), 'Monitoring history requires a valid project and report collection.')
  const byId = new Map()
  const conflicts = new Set()
  for (const report of reports) {
    let safe
    try { safe = safeMonitoringReport(report, projectId) } catch {
      limitations.push('Invalid or foreign-project report metadata was omitted from history.')
      continue
    }
    const old = byId.get(safe.report_id)
    if (old) {
      if (canonical(old) !== canonical(safe)) {
        conflicts.add(safe.report_id)
        limitations.push(`Conflicting duplicate report ID ${safe.report_id} was rejected from history.`)
      } else limitations.push(`Identical duplicate report ID ${safe.report_id} was deduplicated.`)
    } else byId.set(safe.report_id, safe)
  }
  return [...byId.values()].filter(report => !conflicts.has(report.report_id))
}

function snapshotOrder(a, b) {
  return textOrder(a.as_of, b.as_of)
    || textOrder(verifiedTimestamp(a.generated_at) ?? -Infinity, verifiedTimestamp(b.generated_at) ?? -Infinity)
    || textOrder(a.report_id, b.report_id)
}

function coverageLabel(report) {
  const coverage = report.coverage
  if (!record(coverage)) return 'Unknown legacy coverage'
  const ratio = countNumber(coverage.fresh_actual_tasks) && countNumber(coverage.total_tasks) ? ` (${coverage.fresh_actual_tasks}/${coverage.total_tasks} fresh tasks)` : ''
  return `Actual: ${stringOrNull(coverage.actual_status) ?? 'unknown'}${ratio}; risks: ${stringOrNull(coverage.risk_status) ?? 'unknown'}`
}

/** One latest verified snapshot per as_of; points are discrete bars, never projections. */
export function buildMonitoringTrends(reports, projectId) {
  const limitations = ['Discrete saved snapshots only; baseline/method changes must not be connected as a continuous execution trend.']
  const history = historyReports(reports, projectId, limitations).sort(snapshotOrder)
  const groups = new Map()
  for (const report of history) {
    if (!groups.has(report.as_of)) groups.set(report.as_of, [])
    groups.get(report.as_of).push(report)
  }
  const snapshots = []
  for (const [date, group] of groups) {
    const verified = group.filter(report => verifiedTimestamp(report.generated_at) !== null)
    if (group.length > 1) limitations.push(`Limited history: only the latest verified snapshot for as_of ${date} is retained.`)
    if (verified.length !== group.length) limitations.push(`Generated_at metadata unavailable for as_of ${date}; unknown timestamps cannot establish snapshot order.`)
    if (verified.length) snapshots.push(verified[verified.length - 1])
    else if (group.length === 1) snapshots.push(group[0])
    else limitations.push(`Snapshots for as_of ${date} were omitted because no latest timestamp could be verified.`)
  }
  const points = snapshots.map((report, index) => {
    const state = metricState(report)
    const notes = []
    let baselineChanged = false
    if (!record(report.coverage)) notes.push('Unknown legacy coverage')
    if (verifiedTimestamp(report.generated_at) === null) notes.push('Generated timestamp unavailable')
    if (report.tasks.some(task => {
      const baseline = taskBaseline(report, task)
      return baseline.planned_start === null || baseline.due_date === null || baseline.is_critical === null
    })) {
      notes.push('Baseline metadata unavailable')
      limitations.push(`Report ${report.report_id}: planned dates or criticality metadata is unavailable.`)
    }
    if (report.tasks.some(task => dependenciesFor(report, task) === null)) {
      notes.push('Dependency metadata unavailable')
      limitations.push(`Report ${report.report_id}: dependency metadata unavailable; no dependency stability is assumed.`)
    }
    if (state.expectedMethod && report.tasks.some(task => targetBaseline(report, task) === null)) {
      notes.push('Expected target provenance unavailable')
      limitations.push(`Report ${report.report_id}: expected target provenance metadata is unavailable; target baseline comparison eligibility is unknown.`)
    }
    if (!state.complete) limitations.push(`Report ${report.report_id}: actual coverage is incomplete, missing or stale; actual progress is a gap, not a lower-bound estimate.`)
    if (!state.actualMethod) notes.push('Actual method unavailable')
    if (!state.expectedMethod) notes.push('Expected method unavailable')
    if (report.coverage?.planning_freshness !== 'known') limitations.push(`Report ${report.report_id}: planning freshness is ${stringOrNull(report.coverage?.planning_freshness) ?? 'unavailable'}.`)
    if (index) {
      const previous = snapshots[index - 1]
      const baseline = baselineComparison(report, previous)
      baselineChanged = baseline.changed
      if (baseline.changed) notes.push('Baseline changed')
      if (baseline.reasons.some(reason => reason.includes('metadata'))) notes.push('Baseline metadata unavailable')
      limitations.push(...baseline.reasons)
      for (const field of ['actual_progress_method', 'expected_progress_method']) {
        const a = stringOrNull(report.coverage?.[field])
        const b = stringOrNull(previous.coverage?.[field])
        if (a && b && a !== b) {
          notes.push(`${field === 'actual_progress_method' ? 'Actual' : 'Expected'} method changed`)
          limitations.push(`Report ${report.report_id}: ${readable(field)} changed; bars are not like-for-like deltas.`)
        }
      }
    }
    return {
      report_id: report.report_id, as_of: report.as_of,
      generated_at: verifiedTimestamp(report.generated_at) === null ? null : report.generated_at,
      label: [report.as_of, ...uniqueStrings(notes)].join(' — '),
      actual_progress: state.actual_progress, expected_progress: state.expected_progress,
      delayed_tasks: state.delayed_tasks, high_risks: state.high_risks,
      coverage_label: coverageLabel(report), baseline_changed: baselineChanged,
    }
  })
  return { points, limitations: uniqueStrings(limitations) }
}

/** Unknown timestamps, future snapshots and conflicting IDs are never auto-selected. */
export function selectPreviousMonitoringReport(current, reports, projectId) {
  const now = safeMonitoringReport(current, projectId)
  const time = verifiedTimestamp(now.generated_at)
  if (time === null) return null
  const candidates = historyReports(reports, projectId).filter(report => {
    const generated = verifiedTimestamp(report.generated_at)
    return report.report_id !== now.report_id && report.as_of <= now.as_of && generated !== null && generated < time
  }).sort(snapshotOrder)
  return candidates.length ? candidates[candidates.length - 1] : null
}

function safeSourceRef(value) {
  return typeof value === 'string' && value.trim() && !/[\p{Cc}\p{Cf}]/u.test(value)
    && !/^\s*(?:javascript|data|vbscript|file):/i.test(value)
}

function reportSourceRefs(report) {
  const refs = new Set(report.tasks.map(task => `task:${task.task_id}`))
  for (const risk of Array.isArray(report.risks) ? report.risks : []) {
    if (!record(risk) || !identifier(risk.risk_id) || Object.hasOwn(risk, 'project_id') && risk.project_id !== report.project_id) continue
    refs.add(`risk:${risk.risk_id}`)
    if (safeSourceRef(risk.source)) refs.add(risk.source)
  }
  for (const milestone of Array.isArray(report.milestones) ? report.milestones : []) {
    if (record(milestone) && identifier(milestone.milestone_id)) refs.add(`milestone:${milestone.milestone_id}`)
  }
  for (const evidence of [report.retrieved_evidence, report.risk_evidence_used, report.risk_knowledge_sources].flatMap(items => Array.isArray(items) ? items : [])) {
    if (!record(evidence)) continue
    if (identifier(evidence.document_id)) {
      refs.add(`document:${evidence.document_id}`)
      refs.add(evidence.document_id)
    }
    if (safeSourceRef(evidence.source)) refs.add(evidence.source)
  }
  const plan = report.planning_context
  if (report.coverage?.planning_status === 'available') refs.add('planning:snapshot')
  if ([plan?.analysis, plan?.data?.analysis, plan?.source_message?.data?.analysis].some(record)) refs.add('planning:analysis')
  if (plan?.warnings) refs.add('planning:warnings')
  if (record(report.coverage)) {
    refs.add('monitoring:coverage')
    if (report.coverage.actual_status) refs.add('actual:snapshot')
    if (['available', 'stale'].includes(report.coverage.risk_status)) refs.add('risk:snapshot')
    if (report.coverage.risk_status === 'unavailable') refs.add('risk:provider')
  }
  if (stringOrNull(report.status)) refs.add('monitoring:classification')
  return refs
}

/** Passive in-app alerts; recommendations are copied only from an explicit source field. */
export function monitoringAlerts(report, projectId) {
  const safe = safeMonitoringReport(report, projectId)
  const sources = reportSourceRefs(safe)
  const tasks = new Map(safe.tasks.map(task => [task.task_id, task]))
  const boundTasks = ids => uniqueStrings(Array.isArray(ids) ? ids.filter(id => tasks.has(id)) : [])
  const boundRefs = refs => uniqueStrings(Array.isArray(refs) ? refs.filter(ref => safeSourceRef(ref) && sources.has(ref)) : [])
  const alerts = new Map()
  const risks = riskCollection(safe)
  const openRisks = new Map([...(risks ?? new Map())].filter(([, risk]) => risk.status === 'open' && ['high', 'critical'].includes(risk.level) && stringOrNull(risk.description)))
  for (const [id, risk] of openRisks) {
    const action = stringOrNull(risk.recommended_action)
    alerts.set(`risk:${safe.report_id}:${id}`, {
      id: `risk:${safe.report_id}:${id}`, severity: risk.level === 'critical' ? 'critical' : 'warning',
      title: `Open ${risk.level} risk`, message: risk.description,
      task_ids: boundTasks(risk.task_ids), source_refs: boundRefs([`risk:${id}`, risk.source]),
      ...(action ? { recommended_action: action } : {}),
    })
  }
  const warnings = (Array.isArray(safe.warnings) ? safe.warnings : []).filter(record).sort((a, b) => textOrder(canonical(a), canonical(b)))
  const riskWarningUsed = new Set()
  for (const warning of warnings) {
    if (!stringOrNull(warning.code) || !stringOrNull(warning.message) || !['critical', 'warning', 'info'].includes(warning.severity)) continue
    const taskIds = boundTasks(warning.task_ids)
    const refs = boundRefs(warning.source_refs)
    if (['OVERDUE_TASK', 'DELAYED_TASK', 'BLOCKED_TASK'].includes(warning.code)) {
      const ids = uniqueStrings([...taskIds, ...refs.filter(ref => ref.startsWith('task:')).map(ref => ref.slice(5))])
      if (!ids.length || ids.some(id => !freshTask(safe, tasks.get(id)))
        || Array.isArray(warning.task_ids) && warning.task_ids.some(id => !tasks.has(id))
        || Array.isArray(warning.source_refs) && warning.source_refs.some(ref => typeof ref === 'string' && ref.startsWith('task:') && !tasks.has(ref.slice(5)))) continue
      if (warning.code === 'OVERDUE_TASK' && ids.some(id => !countNumber(tasks.get(id).overdue_days) || tasks.get(id).overdue_days <= 0 || statusOf(tasks.get(id)) === 'completed')) continue
      if (warning.code === 'DELAYED_TASK' && ids.some(id => tasks.get(id).delayed !== true || statusOf(tasks.get(id)) === 'completed')) continue
      if (warning.code === 'BLOCKED_TASK' && ids.some(id => statusOf(tasks.get(id)) !== 'blocked')) continue
    }
    if (warning.code === 'HIGH_RISK') {
      // A literal risk.source can collide with another risk's identity; never merge an ambiguous warning.
      const riskRefs = refs.filter(ref => ref.startsWith('risk:') && risks?.has(ref.slice(5)))
      if (riskRefs.length !== 1 || !openRisks.has(riskRefs[0].slice(5))) continue
      const id = `risk:${safe.report_id}:${riskRefs[0].slice(5)}`
      if (riskWarningUsed.has(id)) continue
      const alert = alerts.get(id)
      const action = stringOrNull(warning.recommended_action)
      alerts.set(id, { ...alert, message: warning.message,
        task_ids: uniqueStrings([...alert.task_ids, ...taskIds]), source_refs: uniqueStrings([...alert.source_refs, ...refs]),
        ...(action ? { recommended_action: action } : {}),
      })
      riskWarningUsed.add(id)
      continue
    }
    // Full source identity avoids index-dependent IDs and hash collisions.
    const id = `warning:${safe.report_id}:${encodeURIComponent(canonical([warning.code, warning.message, taskIds, refs]))}`
    const action = stringOrNull(warning.recommended_action)
    const alert = { id, severity: warning.severity, title: readable(warning.code.toLowerCase()).replace(/^./, letter => letter.toUpperCase()), message: warning.message,
      task_ids: taskIds, source_refs: refs, ...(action ? { recommended_action: action } : {}),
    }
    const old = alerts.get(id)
    const rank = { critical: 0, warning: 1, info: 2 }
    if (!old || rank[alert.severity] < rank[old.severity]) alerts.set(id, alert)
  }
  const rank = { critical: 0, warning: 1, info: 2 }
  return [...alerts.values()].sort((a, b) => rank[a.severity] - rank[b.severity] || textOrder(a.id, b.id))
}

function csvCell(value) {
  if (value == null) return ''
  if (knownNumber(value)) return String(value)
  if (typeof value === 'boolean') return String(value)
  if (typeof value !== 'string') return ''
  const prefix = value.match(/^[\s\p{Cc}\p{Cf}]*/u)[0]
  const dangerous = /[\p{Cc}\p{Cf}]/u.test(prefix) || /^[=+\-@]/.test(value.slice(prefix.length))
  const text = dangerous ? `'${value}` : value
  return `"${text.replaceAll('"', '""')}"`
}

/** RFC 4180, a report metadata row plus task rows; unknown values remain empty cells. */
export function reportCsv(report, projectId) {
  const safe = safeMonitoringReport(report, projectId)
  const state = metricState(safe)
  const coverage = safe.coverage
  const metadata = {
    project_id: safe.project_id, report_id: safe.report_id, request_id: safe.request_id,
    as_of: safe.as_of, generated_at: verifiedTimestamp(safe.generated_at) === null ? null : safe.generated_at,
    report_status: stringOrNull(safe.status), coverage_label: coverageLabel(safe),
    planning_status: stringOrNull(coverage?.planning_status), planning_freshness: stringOrNull(coverage?.planning_freshness),
    actual_status: stringOrNull(coverage?.actual_status), risk_status: stringOrNull(coverage?.risk_status),
  }
  for (const field of ['total_tasks', 'fresh_actual_tasks', 'missing_actual_tasks', 'stale_actual_tasks']) metadata[field] = countNumber(coverage?.[field]) ? coverage[field] : null
  for (const field of ['expected_progress_method', 'actual_progress_method']) metadata[field] = stringOrNull(coverage?.[field])
  for (const field of ['planning_updated_on', 'planning_observed_on', 'actual_updated_on', 'actual_observed_on', 'risk_observed_on']) metadata[field] = dateOrNull(coverage?.[field])
  for (const field of deltaFields) metadata[`report_${field}`] = state[field]
  const taskColumns = ['task_id', 'title', 'task_status', 'planned_start', 'due_date', 'actual_start', 'actual_end', 'observed_on', 'task_actual_progress', 'task_expected_progress', 'task_progress_variance', 'is_critical', 'actual_is_stale', 'delayed', 'overdue_days', 'last_observed_progress']
  const columns = ['row_type', ...Object.keys(metadata), ...taskColumns]
  const rows = [{ row_type: 'report', ...metadata }]
  for (const task of safe.tasks) {
    const dates = assessmentDates(task)
    const fresh = freshTask(safe, task)
    rows.push({ row_type: 'task', ...metadata,
      task_id: task.task_id, title: stringOrNull(task.title), task_status: stringOrNull(task.status),
      planned_start: dateOrNull(dates.plannedStart), due_date: dateOrNull(dates.dueDate),
      actual_start: dateOrNull(dates.actualStart), actual_end: dateOrNull(dates.actualEnd), observed_on: dateOrNull(dates.observedOn),
      task_actual_progress: fresh ? task.actual_progress : null,
      task_expected_progress: state.expectedMethod && percentNumber(task.expected_progress) ? task.expected_progress : null,
      task_progress_variance: fresh && state.expectedMethod && knownNumber(task.variance) ? task.variance : null,
      is_critical: typeof task.is_critical === 'boolean' ? task.is_critical : null,
      actual_is_stale: typeof task.actual_is_stale === 'boolean' ? task.actual_is_stale : null,
      delayed: fresh && typeof task.delayed === 'boolean' ? task.delayed : null,
      overdue_days: fresh && countNumber(task.overdue_days) ? task.overdue_days : null,
      last_observed_progress: percentNumber(task.last_observed_progress) ? task.last_observed_progress : null,
    })
  }
  return [columns.map(csvCell).join(','), ...rows.map(row => columns.map(column => csvCell(row[column])).join(','))].join('\r\n') + '\r\n'
}
