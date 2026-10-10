import test from 'node:test'
import assert from 'node:assert/strict'
import {
  assessmentDates, blankActualRows, buildActualProgress, buildMonitoringRequest, coordinatorExport,
  displayProgress, feedbackExport, filterTasks, formatDate, formatNumber,
  formatPercent, formatVariance, highRiskCount, importedPlanning, isCalendarDate,
  localToday, monitoringAccessReadiness, monitoringFailureGuidance, monitoringFailureMetadata,
  plainText, reportExport, reportScope, validateCoordinator,
  buildMonitoringTrends, compareMonitoringReports, monitoringAlerts, reportCsv, selectPreviousMonitoringReport,
} from './monitoringView.js'

const project = 'authorized-project'
const today = '2026-10-09'
const baseline = { project_id: project, fetched_on: '2026-10-08', tasks: [{ task_id: 't1', title: 'Real task' }] }
const request = overrides => buildMonitoringRequest({ projectId: project, requestId: 'real-request-id', asOf: today, baseline, today, ...overrides })
const row = overrides => ({ task_id: 't1', included: true, status: 'IN_PROGRESS', progress_percentage: '30', actual_start: '', actual_end: '', ...overrides })
const actual = rows => buildActualProgress(rows, project, '2026-10-08', today, today)
const planning = () => ({ sender: 'planning_agent', receiver: 'project_monitor_report_agent', action: 'SCHEDULE_CREATED', request_id: 'planning-request', status: 'success', data: { project_id: project, project_name: 'Actual project', analysis: {}, schedule: [{ task_id: 't1', name: 'Real task', planned_start: '2026-10-01', planned_end: '2026-10-20', priority: 'HIGH', dependencies: [], status: 'COMPLETED' }] } })
const report = overrides => ({ project_id: project, report_id: 'r1', request_id: 'req1', as_of: today, metrics: { actual_progress: null, high_risks: 0 }, tasks: [], planning_feedback: [{ sender: 'project_monitor_report_agent', receiver: 'planning_agent', action: 'SCHEDULE_DELAY_DETECTED', request_id: 'req1', data: { project_id: project, task_id: 't1', delay_days: 1 } }], ...overrides })

test('failure metadata retains only status and kind, never private error fields', () => {
  const error = Object.assign(new Error('private-message'), { status: 503, kind: 'http', details: { token: 'private-token' }, token: 'private-token', cause: new Error('private-cause') })
  const metadata = monitoringFailureMetadata(error)
  assert.deepEqual(metadata, { status: 503, kind: 'http' })
  assert.deepEqual(Object.keys(metadata), ['status', 'kind'])
  assert.doesNotMatch(JSON.stringify(metadata), /private/)
  assert.equal(error.token, 'private-token')
})

test('failure metadata safely handles empty, primitive, and unclassified errors', () => {
  for (const error of [null, undefined, '', 'private-message', 401, false, {}, new Error('private-message')]) {
    assert.deepEqual(monitoringFailureMetadata(error), { status: 0, kind: null })
  }
  assert.deepEqual(monitoringFailureMetadata({ status: 401 }), { status: 401, kind: 'http' })
  for (const status of ['401', NaN, Infinity, -1, 99, 600, 401.5, null, {}]) {
    assert.deepEqual(monitoringFailureMetadata({ status, kind: 'network' }), { status: 0, kind: 'network' })
  }
})

test('failure metadata accepts only the declared error kinds', () => {
  for (const kind of ['http', 'network', 'timeout', 'response', 'configuration']) {
    assert.deepEqual(monitoringFailureMetadata({ status: 0, kind }), { status: 0, kind })
  }
  for (const kind of ['private-token', {}, [], null, undefined]) {
    assert.deepEqual(monitoringFailureMetadata({ status: 0, kind }), { status: 0, kind: null })
  }
})

test('empty failures have no guidance or network troubleshooting', () => {
  assert.equal(monitoringFailureGuidance(null), null)
  assert.equal(monitoringFailureGuidance(undefined), null)
})

test('HTTP 401 guidance requires signing in again, not operator tokens or network retries', () => {
  const guidance = monitoringFailureGuidance({ status: 401, kind: 'http' })
  assert.match(guidance.title, /access rejected.*401/)
  assert.match(guidance.message, /signed-in session.*Sign in again/)
    assert.doesNotMatch(guidance.message, /identity mapping|scoped Monitoring token/)
  assert.equal(guidance.showConnectionHelp, false)
})

test('HTTP 403 guidance distinguishes project/permission denial from an invalid token', () => {
  const guidance = monitoringFailureGuidance({ status: 403, kind: 'http' })
  assert.match(guidance.title, /access denied.*403/)
  assert.match(guidance.message, /project.*operation/)
  assert.match(guidance.message, /ownership.*permissions/)
    assert.match(guidance.message, /does not sign out/)
  assert.equal(guidance.showConnectionHelp, false)
})

test('HTTP 404 guidance describes project/artifact access without logging out', () => {
  const guidance = monitoringFailureGuidance({ status: 404, kind: 'http' })
  assert.match(guidance.title, /project or artifact unavailable.*404/)
  assert.match(guidance.message, /signed-in account.*Refresh project access/)
  assert.match(guidance.message, /does not sign out/)
  assert.equal(guidance.showConnectionHelp, false)
})

test('HTTP 503 guidance says the backend responded and needs operator configuration', () => {
  const guidance = monitoringFailureGuidance({ status: 503, kind: 'http' })
  assert.match(guidance.title, /service configuration required.*503/)
  assert.match(guidance.message, /backend responded/)
  assert.match(guidance.message, /operator.*signed-in authentication.*source configuration/)
  assert.match(guidance.message, /Check API/)
  assert.equal(guidance.showConnectionHelp, false)
  assert.doesNotMatch(guidance.message, /CORS|proxy|cannot reach/i)
})

test('other HTTP failures retain their status without recommending connection checks', () => {
  for (const status of [409, 422, 500, 502, 504]) {
    const guidance = monitoringFailureGuidance({ status, kind: 'http' })
    assert.match(guidance.title, new RegExp(`HTTP ${status}`))
    assert.match(guidance.message, /returned an HTTP response/)
    assert.equal(guidance.showConnectionHelp, false)
  }
})

test('an HTTP status overrides conflicting network or timeout classification', () => {
  for (const status of [401, 403, 503, 504]) {
    for (const kind of ['network', 'timeout']) {
      const guidance = monitoringFailureGuidance({ status, kind })
      assert.equal(guidance.showConnectionHelp, false)
      assert.match(guidance.title, new RegExp(`HTTP ${status}`))
    }
  }
})

test('response mismatches require API compatibility and metadata checks, not network help', () => {
  const guidance = monitoringFailureGuidance({ status: 0, kind: 'response' })
  assert.match(guidance.title, /response could not be verified/)
  assert.match(guidance.message, /malformed.*project or report/)
  assert.match(guidance.message, /No response data was used.*API compatibility.*metadata/)
  assert.equal(guidance.showConnectionHelp, false)
})

test('an HTTP success status does not hide response or configuration mismatches', () => {
  for (const kind of ['response', 'configuration']) {
    const guidance = monitoringFailureGuidance({ status: 200, kind })
    assert.equal(guidance.title, monitoringFailureGuidance({ status: 0, kind }).title)
    assert.equal(guidance.showConnectionHelp, false)
    assert.deepEqual(monitoringFailureMetadata({ status: 200, kind }), { status: 200, kind })
  }
})

test('frontend configuration failures require configuration changes, not sign-in retries', () => {
  const guidance = monitoringFailureGuidance({ status: 0, kind: 'configuration' })
  assert.match(guidance.title, /configuration needs attention/)
  assert.match(guidance.message, /configured API URL.*frontend\/backend settings/)
  assert.match(guidance.message, /Repeated sign-in attempts will not fix/)
  assert.equal(guidance.showConnectionHelp, false)
})

test('only network and timeout failures without an HTTP status invite connection help', () => {
  for (const kind of ['http', 'network', 'timeout', 'response', 'configuration', null, 'unknown']) {
    const guidance = monitoringFailureGuidance({ status: 0, kind })
    assert.equal(guidance.showConnectionHelp, kind === 'network' || kind === 'timeout')
  }
  assert.match(monitoringFailureGuidance({ kind: 'network' }).message, /No usable HTTP response/)
  const timeout = monitoringFailureGuidance({ kind: 'timeout' })
  assert.match(timeout.title, /timed out/)
  assert.match(timeout.message, /save may still finish.*check report history/)
})

test('unknown failures do not fabricate a network diagnosis or echo raw details', () => {
  const guidance = monitoringFailureGuidance({ message: 'private-message', details: 'private-token' })
  assert.equal(guidance.showConnectionHelp, false)
  assert.match(guidance.message, /No verified result/)
  assert.doesNotMatch(JSON.stringify(guidance), /private/)
})

test('health authentication diagnostics neither grant nor block signed-in verification', () => {
  const snapshot = { status: 'configuration_required', live_sources_verified: false, capabilities: { auth_configured: false, planning_configured: true, risk_configured: true, storage_configured: true } }
  const readiness = monitoringAccessReadiness(snapshot)
  assert.equal(readiness.authMissing, true)
  assert.equal(readiness.canVerify, true)
  assert.match(readiness.message, /protected Monitoring session endpoint.*no project access is inferred/)
  assert.match(readiness.message, /operator.*signed-in authentication support/)
  assert.doesNotMatch(readiness.message, /MONITORING_AUTH_IDENTITIES|scoped token/)
  assert.match(readiness.message, /Check API/)
  assert.equal(monitoringAccessReadiness({ ...snapshot, status: 'ready' }).canVerify, true)
  assert.equal(readiness.project_ids, undefined)
})

test('configured authentication permits verification, not fabricated project access', () => {
  const readiness = monitoringAccessReadiness({ status: 'ready', capabilities: { auth_configured: true, planning_configured: false } })
  assert.deepEqual(readiness, { authMissing: false, canVerify: true, message: '' })
  assert.equal(readiness.project_ids, undefined)
  assert.equal(readiness.permissions, undefined)
})

test('unknown authentication is not coerced to missing or presented as verified access', () => {
  for (const snapshot of [null, undefined, {}, { capabilities: {} }, { capabilities: { auth_configured: 'false' } }, { capabilities: { auth_configured: 0 } }]) {
    assert.deepEqual(monitoringAccessReadiness(snapshot), { authMissing: false, canVerify: true, message: '' })
  }
})

test('numeric formatting never coerces unknown into zero', () => {
  for (const value of [null, undefined, '', '0', NaN, Infinity]) {
    assert.equal(formatNumber(value), 'Unknown')
    assert.equal(formatPercent(value), 'Unknown')
    assert.equal(formatVariance(value), 'Unknown')
    assert.equal(displayProgress(value), null)
  }
  assert.equal(formatPercent(0), '0%')
  assert.equal(formatPercent(100), '100%')
  assert.equal(formatVariance(2), '+2 pp')
  assert.equal(formatVariance(-2), '-2 pp')
})

test('display bars clamp known numbers only, without changing metrics', () => {
  assert.equal(displayProgress(-4), 0)
  assert.equal(displayProgress(112), 100)
  assert.equal(displayProgress(40), 40)
  assert.equal(formatPercent(112), '112%')
})

test('today uses local calendar getters and never the UTC ISO date', () => {
  const local = { getFullYear: () => 2026, getMonth: () => 0, getDate: () => 2, toISOString: () => '2026-01-01T23:00:00Z' }
  assert.equal(localToday(local), '2026-01-02')
})

test('calendar validation rejects invalid, rolled, and timestamp dates', () => {
  for (const date of ['2026-02-29', '2026-04-31', '2026-13-01', '2026-00-01', '2026-1-01', '2026-01-01T00:00:00Z', null]) assert.equal(isCalendarDate(date), false)
  assert.equal(isCalendarDate('2024-02-29'), true)
  assert.equal(formatDate(null), 'Unknown')
  assert.equal(formatDate('2026-02-30'), 'Unknown')
  assert.equal(formatDate('2026-10-09'), new Date(2026, 9, 9, 12).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }))
})

test('task dates use report-owned fields, not the current baseline', () => {
  assert.deepEqual(assessmentDates({ planned_start: '2026-10-01', due_date: '2026-10-20', actual_start: '2026-10-03', completed_on: '2026-10-08', last_updated_on: '2026-10-08' }), { plannedStart: '2026-10-01', dueDate: '2026-10-20', actualStart: '2026-10-03', actualEnd: '2026-10-08', observedOn: '2026-10-08' })
  assert.deepEqual(assessmentDates({}), { plannedStart: null, dueDate: null, actualStart: null, actualEnd: null, observedOn: null })
})

test('new actual rows are blank even if Planning claims completed status', () => {
  const rows = blankActualRows([{ task_id: 't1', status: 'COMPLETED', expected_progress: 100 }])
  assert.deepEqual(rows, [{ task_id: 't1', included: false, status: '', progress_percentage: '', actual_start: '', actual_end: '' }])
  assert.equal(actual(rows), undefined)
})

test('no observation is missing, not an empty invented actual snapshot', () => {
  assert.equal(buildActualProgress([], project, '', today, today), undefined)
  const body = request()
  assert.ok(!Object.hasOwn(body, 'actual_progress'))
  assert.ok(!Object.hasOwn(body, 'observed_on'))
  assert.equal(body.include_risks, true)
  assert.equal(body.save_report, false)
})

test('unchecked observations are ignored, not promoted automatically', () => {
  assert.equal(actual([row({ included: false })]), undefined)
  assert.equal(actual([{ task_id: 't1', status: 'COMPLETED', progress_percentage: '100' }]), undefined)
})

test('included rows require explicit progress; empty strings and null are not zero', () => {
  for (const progress_percentage of ['', ' ', null, undefined, NaN, 'invalid', -1, 101, false]) {
    assert.throws(() => actual([row({ progress_percentage })]), /explicit progress/)
  }
  assert.throws(() => actual([row({ status: '' })]), /explicit actual status/)
  assert.throws(() => actual([row({ status: 'in_progress' })]), /explicit actual status/)
})

test('real numeric progress is sent in the actual API envelope', () => {
  const body = request({ rows: [row({ actual_start: '2026-10-01' })], observedOn: '2026-10-08' })
  assert.deepEqual(body.actual_progress, { project_id: project, tasks: [{ task_id: 't1', status: 'IN_PROGRESS', progress_percentage: 30, actual_start: '2026-10-01' }] })
  assert.equal(body.observed_on, '2026-10-08')
  assert.equal(baseline.tasks[0].actual_start, undefined)
})

test('completed tasks require explicit 100% and an end date', () => {
  assert.throws(() => actual([row({ status: 'COMPLETED', progress_percentage: 99, actual_end: '2026-10-08' })]), /100%/)
  assert.throws(() => actual([row({ status: 'COMPLETED', progress_percentage: 100 })]), /end date/)
  assert.equal(actual([row({ status: 'COMPLETED', progress_percentage: 100, actual_end: '2026-10-08' })]).tasks[0].actual_end, '2026-10-08')
})

test('incomplete and not-started statuses must be consistent with measurements', () => {
  assert.throws(() => actual([row({ progress_percentage: 100 })]), /Only completed/)
  assert.throws(() => actual([row({ actual_end: '2026-10-08' })]), /Only completed/)
  assert.throws(() => actual([row({ status: 'NOT_STARTED', progress_percentage: 1 })]), /zero progress/)
  assert.throws(() => actual([row({ status: 'NOT_STARTED', progress_percentage: 0, actual_start: '2026-10-01' })]), /start date/)
  assert.equal(actual([row({ status: 'NOT_STARTED', progress_percentage: '0' })]).tasks[0].progress_percentage, 0)
})

test('recording dates are required, not future, and not later than the report', () => {
  for (const observedOn of ['', '2026-02-30', '2026-10-10']) assert.throws(() => request({ rows: [row()], observedOn }), /recording date/)
  assert.throws(() => request({ asOf: '2026-10-08', rows: [row()], observedOn: today }), /recording date/)
})

test('actual dates cannot postdate their observation or end before start', () => {
  assert.throws(() => actual([row({ actual_start: '2026-10-09' })]), /recording date/)
  assert.throws(() => actual([row({ actual_start: 'not-a-date' })]), /dates/)
  assert.throws(() => actual([row({ status: 'COMPLETED', progress_percentage: 100, actual_start: '2026-10-08', actual_end: '2026-10-07' })]), /after actual end/)
})

test('duplicate or unknown actual tasks cannot enter the request', () => {
  assert.throws(() => actual([row(), row()]), /unique/)
  assert.throws(() => request({ rows: [row({ task_id: 'other-task' })], observedOn: today }), /reference tasks/)
})

test('request cannot use a live baseline fetched after the report date', () => {
  assert.throws(() => request({ asOf: '2026-10-07' }), /fetched after/)
  assert.throws(() => request({ baseline: { ...baseline, fetched_on: null } }), /dated Planning/)
  assert.throws(() => request({ baseline: { ...baseline, project_id: 'other' } }), /baseline/)
  assert.throws(() => request({ asOf: '2026-10-10' }), /no later than today/)
})

test('request only contains approved fields, no identity or destination inputs', () => {
  const body = request({ principal: 'invented', permissions: ['monitoring:generate'], token: 'secret', destination: 'https://other', query: '  deadlines  ' })
  assert.deepEqual(Object.keys(body).sort(), ['as_of', 'include_risks', 'project_id', 'query', 'request_id', 'save_report'])
  assert.equal(body.query, 'deadlines')
})

test('save is explicit and storage-gated without blocking unsaved generation', () => {
  assert.throws(() => request({ saveReport: true }), /storage/)
  assert.equal(request({ saveReport: true, storageConfigured: true }).save_report, true)
  assert.equal(request({ includeRisks: false }).include_risks, false)
  assert.throws(() => request({ includeRisks: 'true' }), /booleans/)
  assert.throws(() => request({ query: 'x'.repeat(501) }), /500/)
})

test('real imported Planning message creates only a baseline, not actuals', () => {
  const text = JSON.stringify(planning())
  const parsed = importedPlanning(text, project, '2026-10-06')
  assert.equal(parsed.baseline.tasks[0].title, 'Real task')
  assert.equal(parsed.baseline.tasks[0].status, undefined)
  assert.equal(parsed.baseline.tasks[0].actual_progress, undefined)
  const body = request({ asOf: '2026-10-07', planningText: text, receivedOn: '2026-10-06' })
  assert.deepEqual(body.planning_message, planning())
  assert.equal(body.received_on, '2026-10-06')
  assert.equal(body.actual_progress, undefined)
})

test('Planning preview preserves explicit critical flags independently of priority', () => {
  for (const is_critical of [true, false]) {
    const message = planning()
    message.data.schedule[0].is_critical = is_critical
    message.data.analysis.critical_path = ['t1']
    assert.equal(importedPlanning(JSON.stringify(message), project, today).baseline.tasks[0].is_critical, is_critical)
  }
  const message = planning()
  message.data.schedule[0].priority = 'LOW'
  message.data.analysis.critical_path = ['t1']
  assert.equal(importedPlanning(JSON.stringify(message), project, today).baseline.tasks[0].is_critical, true)
  delete message.data.analysis.critical_path
  message.data.schedule[0].priority = 'HIGH'
  assert.equal(importedPlanning(JSON.stringify(message), project, today).baseline.tasks[0].is_critical, false)
})

test('Planning import rejects invalid critical metadata instead of guessing', () => {
  for (const critical_path of [null, 't1', ['unknown'], ['t1', 't1']]) {
    const message = planning()
    message.data.analysis.critical_path = critical_path
    assert.throws(() => importedPlanning(JSON.stringify(message), project, today), /critical path/)
  }
  const message = planning()
  message.data.schedule[0].is_critical = 'true'
  assert.throws(() => importedPlanning(JSON.stringify(message), project, today), /critical flags/)
})

test('Planning import rejects malformed JSON, mismatches, missing receipt, and future receipt', () => {
  assert.throws(() => importedPlanning('{bad', project, today), /valid JSON/)
  assert.throws(() => importedPlanning(JSON.stringify(planning()), 'other', today), /selected project/)
  assert.throws(() => importedPlanning(JSON.stringify(planning()), project, ''), /received/)
  assert.throws(() => request({ planningText: JSON.stringify(planning()), receivedOn: '2026-10-10' }), /receipt date/)
  const bad = planning()
  bad.action = 'CREATE_SCHEDULE'
  assert.throws(() => importedPlanning(JSON.stringify(bad), project, today), /SCHEDULE_CREATED/)
})

test('Planning import rejects duplicate IDs and unknown dependencies', () => {
  const duplicate = planning()
  duplicate.data.schedule.push(duplicate.data.schedule[0])
  assert.throws(() => importedPlanning(JSON.stringify(duplicate), project, today), /unique/)
  const bad = planning()
  bad.data.schedule[0].dependencies = ['unknown']
  assert.throws(() => importedPlanning(JSON.stringify(bad), project, today), /dependencies/)
})

test('task filters distinguish confirmed delays from upstream dependency concerns', () => {
  const tasks = [{ task_id: 't1', title: 'Alpha', status: 'missing' }, { task_id: 't2', title: 'Beta', status: 'in_progress', actual_is_stale: true }, { task_id: 't3', title: 'Gamma', status: 'blocked', delayed: true }, { task_id: 't4', title: 'Delta', status: 'in_progress', dependency_delayed: true }]
  assert.deepEqual(filterTasks(tasks, 'alpha', 'missing').map(task => task.task_id), ['t1'])
  assert.deepEqual(filterTasks(tasks, '', 'stale').map(task => task.task_id), ['t2'])
  assert.deepEqual(filterTasks(tasks, 'T3', 'blocked').map(task => task.task_id), ['t3'])
  assert.deepEqual(filterTasks(tasks, '', 'delayed').map(task => task.task_id), ['t3'])
  assert.deepEqual(filterTasks(tasks, '', 'dependency').map(task => task.task_id), ['t4'])
  assert.equal(filterTasks(tasks, 'not found').length, 0)
})

test('missing, stale, and completed dependency concerns are not confirmed execution delays', () => {
  const tasks = ['missing', 'stale', 'completed'].map(status => ({ task_id: status, title: status, status, delayed: false, dependency_delayed: true }))
  assert.deepEqual(filterTasks(tasks, '', 'delayed'), [])
  assert.deepEqual(filterTasks(tasks, '', 'dependency'), tasks)
})

test('risk zero is known only with available risk coverage, not legacy scope', () => {
  for (const coverage of [undefined, null, { risk_status: 'not_supplied' }, { risk_status: 'stale' }, { risk_status: 'unavailable' }]) assert.equal(highRiskCount(report({ coverage })), null)
  assert.equal(highRiskCount(report({ coverage: { risk_status: 'available' } })), 0)
  assert.equal(highRiskCount(report({ coverage: { risk_status: 'available' }, metrics: { high_risks: 2 } })), 2)
  assert.equal(highRiskCount(report({ coverage: { risk_status: 'available' }, metrics: { high_risks: null } })), null)
  assert.match(reportScope(report()), /Unknown legacy scope/)
  assert.match(reportScope(report({ coverage: null })), /Unknown legacy scope/)
  assert.equal(reportScope(report({ coverage: {} })), 'Based on available inputs')
})

test('evidence is plain text; no HTML transformation', () => {
  assert.equal(plainText('<script>private source text</script>'), '<script>private source text</script>')
  assert.equal(plainText(null), 'Unknown')
  assert.equal(plainText({ source: 'real' }), '{\n  "source": "real"\n}')
})

test('report exports are metadata-bound and omit unrecognized and secret fields', () => {
  const value = reportExport(report({ access_token: 'never-export', planning_context: { api_key: 'secret', nested: { authorization: 'secret', task: 'keep' } }, internal_private: 'omit' }), project)
  assert.ok(!JSON.stringify(value).includes('secret'))
  assert.equal(value.access_token, undefined)
  assert.equal(value.internal_private, undefined)
  assert.equal(value.planning_context.nested.task, 'keep')
  assert.equal(value.metrics.actual_progress, null)
  assert.throws(() => reportExport(report(), 'other'), /metadata/)
  assert.throws(() => reportExport(report({ request_id: '' }), project), /metadata/)
})

test('report and Coordinator exports preserve actual provenance and redact encoded credentials', () => {
  const actual_context = { source_payload: { project_id: project, observed_on: today, tasks: [{ task_id: 't1', progress_percentage: 30, accessToken: 'credential-value', clientSecret: 'credential-value', HTTPAuthorization: 'credential-value' }] }, snapshot_date_basis: 'observation', task_date_basis: { t1: 'observation' }, metadata: JSON.stringify({ apiKey: 'credential-value', evidence: 'Keep evidence' }), encoded: JSON.stringify(JSON.stringify({ refreshToken: 'credential-value', note: 'Keep note' })) }
  const source = report({ actual_context, planning_context: { source_message: { request_id: 'planning-request', data: { serviceRoleKey: 'credential-value', title: 'Keep title' } } } })
  const safe = reportExport(source, project)
  assert.equal(safe.actual_context.source_payload.tasks[0].progress_percentage, 30)
  assert.equal(safe.actual_context.snapshot_date_basis, 'observation')
  assert.deepEqual(safe.actual_context.task_date_basis, { t1: 'observation' })
  assert.deepEqual(JSON.parse(safe.actual_context.metadata), { evidence: 'Keep evidence' })
  assert.deepEqual(JSON.parse(JSON.parse(safe.actual_context.encoded)), { note: 'Keep note' })
  assert.equal(safe.planning_context.source_message.request_id, 'planning-request')
  assert.ok(!JSON.stringify(safe).includes('credential-value'))
  assert.deepEqual(coordinatorExport(source, project).report.actual_context, safe.actual_context)
  assert.equal(actual_context.source_payload.tasks[0].accessToken, 'credential-value')
})

test('Planning feedback exports include project/report/request metadata and validated envelopes', () => {
  const value = feedbackExport(report(), project)
  assert.equal(value.project_id, project)
  assert.equal(value.report_id, 'r1')
  assert.equal(value.request_id, 'req1')
  assert.equal(value.planning_feedback[0].data.task_id, 't1')
  assert.throws(() => feedbackExport(report({ planning_feedback: [{ ...report().planning_feedback[0], request_id: 'other' }] }), project), /metadata/)
  assert.throws(() => feedbackExport(report({ planning_feedback: [{ ...report().planning_feedback[0], data: { project_id: 'other' } }] }), project), /metadata/)
  assert.deepEqual(feedbackExport(report({ planning_feedback: [] }), project).planning_feedback, [])
})

test('Coordinator messages preserve correlation and validate saved report metadata', () => {
  const value = coordinatorExport(report(), project)
  assert.equal(value.message_type, 'monitoring.report.ready')
  assert.equal(value.sender, 'monitoring_reporting')
  assert.equal(value.recipient, 'coordinator')
  assert.equal(value.correlation_id, 'req1')
  assert.equal(value.report.project_id, project)
  assert.deepEqual(validateCoordinator(value, project, 'r1'), value)
  assert.throws(() => validateCoordinator({ ...value, project_id: 'other' }, project, 'r1'), /metadata/)
  assert.throws(() => validateCoordinator({ ...value, correlation_id: 'other' }, project, 'r1'), /metadata/)
  assert.throws(() => validateCoordinator(value, project, 'other-report'), /metadata/)
})

const assessedTask = overrides => ({
  task_id: 't1', title: 'Real task', status: 'in_progress', actual_progress: 20,
  expected_progress: 50, variance: -30, planned_start: '2026-10-01', due_date: '2026-10-20',
  actual_start: '2026-10-02', completed_on: null, last_updated_on: '2026-10-07',
  is_critical: false, actual_is_stale: false, delayed: true, overdue_days: 0, ...overrides,
})
const snapshot = (overrides = {}) => {
  const tasks = overrides.tasks ?? [assessedTask()]
  return report({
    report_id: 'current', generated_at: '2026-10-09T12:00:00Z', risks: [], warnings: [], ...overrides, tasks,
    metrics: { total_tasks: tasks.length, actual_progress: 20, expected_progress: 50, progress_variance: -30,
      delayed_tasks: 1, blocked_tasks: 0, overdue_tasks: 0, high_risks: 0, actual_progress_is_lower_bound: false, ...overrides.metrics },
    coverage: overrides.coverage === null ? null : {
      planning_status: 'available', planning_freshness: 'known', actual_status: 'complete', risk_status: 'available',
      total_tasks: tasks.length, fresh_actual_tasks: tasks.length, missing_actual_tasks: 0, stale_actual_tasks: 0,
      expected_progress_method: 'calendar_estimate', actual_progress_method: 'unweighted_task_average', ...overrides.coverage,
    },
  })
}
const previousSnapshot = overrides => snapshot({ report_id: 'previous', as_of: '2026-10-08', generated_at: '2026-10-08T12:00:00Z', ...overrides })
const sourceRisk = (id, overrides) => ({ risk_id: id, description: `Source risk ${id}`, level: 'high', status: 'open', task_ids: ['t1'], ...overrides })
const compare = (current = snapshot(), previous = previousSnapshot()) => compareMonitoringReports(current, previous, project)
const mixedPlanning = (target = 50) => ({ tasks: [
  { task_id: 't1', expected_progress: target, dependency_ids: [] },
  { task_id: 't2', expected_progress: null, dependency_ids: [] },
] })

function parseCsv(csv) {
  const rows = []
  let row = []
  let cell = ''
  let quoted = false
  for (let index = 0; index < csv.length; index += 1) {
    const char = csv[index]
    if (char === '"') {
      if (quoted && csv[index + 1] === '"') { cell += '"'; index += 1 }
      else quoted = !quoted
    } else if (!quoted && char === ',') { row.push(cell); cell = '' }
    else if (!quoted && char === '\r' && csv[index + 1] === '\n') {
      row.push(cell); rows.push(row); row = []; cell = ''; index += 1
    } else cell += char
  }
  assert.equal(quoted, false, 'CSV has balanced RFC 4180 quotes')
  assert.equal(cell, '', 'CSV terminates its last row')
  assert.equal(row.length, 0)
  return rows
}
const csvRecords = csv => {
  const [headers, ...rows] = parseCsv(csv)
  for (const row of rows) assert.equal(row.length, headers.length, 'All CSV rows have the same columns')
  return rows.map(row => Object.fromEntries(headers.map((header, index) => [header, row[index]])))
}

function freezeInput(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freezeInput)
    Object.freeze(value)
  }
  return value
}

test('comparison returns the frozen contract and real per-metric deltas, including zero', () => {
  const current = snapshot({ tasks: [assessedTask({ actual_progress: 40 })], metrics: { actual_progress: 40, expected_progress: 60, progress_variance: -20, delayed_tasks: 0 } })
  const result = compare(current)
  assert.deepEqual(Object.keys(result), ['current_report_id', 'previous_report_id', 'comparable', 'reasons', 'deltas', 'task_changes', 'risk_changes'])
  assert.equal(result.current_report_id, 'current')
  assert.equal(result.previous_report_id, 'previous')
  assert.equal(result.comparable, true)
  assert.deepEqual(result.deltas, { actual_progress: 20, expected_progress: 10, progress_variance: 10, delayed_tasks: -1, blocked_tasks: 0, overdue_tasks: 0, high_risks: 0 })
  assert.deepEqual(result.task_changes, [{ task_id: 't1', title: 'Real task', from_status: 'in_progress', to_status: 'in_progress', progress_delta: 20, change: 'progress_changed' }])
  assert.deepEqual(result.risk_changes, { new: [], no_longer_returned: [], status_changed: [], assessment_available: true })
  assert.match(result.reasons.join(' '), /Dependency metadata unavailable/)
})

test('comparison refuses absent, identical, future and unverifiable previous snapshots', () => {
  const missing = compareMonitoringReports(snapshot(), null, project)
  assert.equal(missing.previous_report_id, null)
  assert.equal(missing.comparable, false)
  assert.match(missing.reasons.join(' '), /No previous/)
  for (const old of [snapshot(), previousSnapshot({ as_of: '2026-10-10' }), previousSnapshot({ generated_at: null }), previousSnapshot({ generated_at: '2026-10-10T12:00:00Z' }), previousSnapshot({ generated_at: '2026-02-30T12:00:00Z' }), previousSnapshot({ generated_at: '2026-10-08T12:00:00' })]) {
    const result = compare(snapshot(), old)
    assert.equal(result.comparable, false)
    assert.ok(result.reasons.length)
    assert.ok(Object.values(result.deltas).every(value => value === null))
    assert.deepEqual(result.task_changes, [])
    assert.equal(result.risk_changes.assessment_available, false)
  }
})

test('single-report helpers enforce project, source, ID, date and task metadata bindings', () => {
  const invalid = [snapshot({ project_id: 'foreign' }), snapshot({ request_id: '' }), snapshot({ report_id: '../bad' }), snapshot({ as_of: '2026-02-30' }), snapshot({ tasks: [assessedTask(), assessedTask()] }), snapshot({ tasks: [assessedTask({ task_id: '../bad' })] }), snapshot({ planning_context: { source_message: { data: { project_id: 'foreign' } } } }), snapshot({ actual_context: { source_payload: { project_id: 'foreign' } } }), snapshot({ tasks: [assessedTask({ project_id: 'foreign' })] }), snapshot({ risks: [sourceRisk('risk1', { project_id: 'foreign' })] })]
  for (const value of invalid) {
    assert.throws(() => compare(value), /metadata/)
    assert.throws(() => compare(snapshot(), value), /metadata/)
    assert.throws(() => monitoringAlerts(value, project), /metadata/)
    assert.throws(() => reportCsv(value, project), /metadata/)
    assert.throws(() => selectPreviousMonitoringReport(value, [], project), /metadata/)
  }
  for (const projectId of [null, '', '../foreign']) {
    assert.throws(() => reportCsv(snapshot(), projectId), /project/)
    assert.throws(() => buildMonitoringTrends([], projectId), /project/)
  }
})

test('foreign project metadata in every evidence collection is rejected before helpers bind references', () => {
  for (const field of ['retrieved_evidence', 'risk_evidence_used', 'risk_knowledge_sources']) {
    const value = freezeInput(snapshot({
      [field]: [{ project_id: 'foreign', document_id: 'foreign-doc', source: 'foreign-document.pdf' }],
      warnings: [{ code: 'SOURCE_WARNING', severity: 'info', message: 'Review evidence', source_refs: ['document:foreign-doc', 'foreign-document.pdf'] }],
    }))
    assert.throws(() => monitoringAlerts(value, project), /source metadata/)
    assert.throws(() => compare(value), /source metadata/)
    assert.throws(() => compare(snapshot(), value), /source metadata/)
    assert.throws(() => reportCsv(value, project), /source metadata/)
    assert.throws(() => selectPreviousMonitoringReport(value, [], project), /source metadata/)
    const trends = buildMonitoringTrends([value], project)
    assert.deepEqual(trends.points, [])
    assert.match(trends.limitations.join(' '), /foreign-project report metadata was omitted/)
    assert.equal(selectPreviousMonitoringReport(snapshot(), [{ ...value, report_id: 'foreign-previous', generated_at: '2026-10-08T12:00:00Z' }], project), null)
    assert.equal(value[field][0].project_id, 'foreign')
  }
})

test('matching and untagged legacy evidence retains its recorded source references', () => {
  for (const field of ['retrieved_evidence', 'risk_evidence_used', 'risk_knowledge_sources']) {
    for (const metadata of [{ project_id: project }, {}]) {
      const value = snapshot({
        [field]: [{ ...metadata, document_id: 'doc1', source: 'source.pdf' }, 'Legacy source text'],
        warnings: [{ code: 'SOURCE_WARNING', severity: 'info', message: 'Recorded evidence', source_refs: ['document:doc1', 'source.pdf'] }],
      })
      assert.deepEqual(monitoringAlerts(value, project)[0].source_refs, ['document:doc1', 'source.pdf'])
      assert.equal(buildMonitoringTrends([value], project).points.length, 1)
    }
  }
})

test('legacy coverage never promotes recorded zeros or percentages into known metrics', () => {
  const legacy = snapshot({ coverage: null, metrics: { actual_progress: 0, expected_progress: 0, progress_variance: 0, delayed_tasks: 0 } })
  const result = compare(legacy, previousSnapshot({ coverage: null }))
  assert.equal(result.comparable, false)
  assert.ok(Object.values(result.deltas).every(value => value === null))
  assert.equal(result.risk_changes.assessment_available, false)
  assert.match(result.reasons.join(' '), /Unknown legacy coverage/)
  const point = buildMonitoringTrends([legacy], project).points[0]
  assert.deepEqual([point.actual_progress, point.expected_progress, point.delayed_tasks, point.high_risks], [null, null, null, null])
  assert.match(point.coverage_label, /Unknown legacy/)
})

test('partial, stale, missing, inconsistent and lower-bound actual coverage are gaps', () => {
  const cases = [
    { coverage: { actual_status: 'partial', fresh_actual_tasks: 0, missing_actual_tasks: 1 } },
    { coverage: { actual_status: 'stale', stale_actual_tasks: 1 } },
    { coverage: { actual_status: 'missing', missing_actual_tasks: 1 } },
    { coverage: { fresh_actual_tasks: 0 } }, { coverage: { total_tasks: 2 } },
    { metrics: { actual_progress_is_lower_bound: true } },
    { tasks: [assessedTask({ status: 'missing', actual_progress: null })] },
    { tasks: [assessedTask({ status: 'stale', actual_is_stale: true })] },
    { tasks: [assessedTask({ actual_progress: '20%' })] },
    { tasks: [assessedTask({ last_updated_on: '2026-10-10' })] },
  ]
  for (const overrides of cases) {
    const value = snapshot(overrides)
    const result = compare(value)
    for (const key of ['actual_progress', 'progress_variance', 'delayed_tasks', 'blocked_tasks', 'overdue_tasks']) assert.equal(result.deltas[key], null, key)
    assert.equal(result.deltas.expected_progress, 0)
    assert.equal(result.deltas.high_risks, 0)
    assert.equal(result.comparable, true, 'Risk and expected metrics remain independently usable')
    assert.ok(result.task_changes.every(change => change.progress_delta === null))
    const point = buildMonitoringTrends([value], project).points[0]
    assert.equal(point.actual_progress, null)
    assert.equal(point.delayed_tasks, null)
    assert.equal(point.high_risks, 0)
  }
})

test('typed metrics are required; percentage strings, null, NaN and infinity are not numbers', () => {
  for (const unknown of [null, '', '0', '20%', NaN, Infinity]) {
    const result = compare(snapshot({ metrics: { actual_progress: unknown, expected_progress: unknown, progress_variance: unknown, delayed_tasks: unknown, blocked_tasks: unknown, overdue_tasks: unknown, high_risks: unknown } }))
    assert.ok(Object.values(result.deltas).every(value => value === null))
    const point = buildMonitoringTrends([snapshot({ metrics: { actual_progress: unknown, expected_progress: unknown, delayed_tasks: unknown, high_risks: unknown } })], project).points[0]
    assert.deepEqual([point.actual_progress, point.expected_progress, point.delayed_tasks, point.high_risks], [null, null, null, null])
  }
})

test('method changes gate only their affected metrics', () => {
  const expectedChanged = compare(snapshot({ coverage: { expected_progress_method: 'supplied_target' } }))
  assert.equal(expectedChanged.deltas.actual_progress, 0)
  assert.equal(expectedChanged.deltas.expected_progress, null)
  assert.equal(expectedChanged.deltas.progress_variance, null)
  assert.equal(expectedChanged.deltas.delayed_tasks, null)
  assert.equal(expectedChanged.deltas.blocked_tasks, 0)
  const actualChanged = compare(snapshot({ coverage: { actual_progress_method: 'weighted_average' } }))
  assert.equal(actualChanged.deltas.actual_progress, null)
  assert.equal(actualChanged.deltas.expected_progress, 0)
  assert.equal(actualChanged.deltas.blocked_tasks, 0)
  assert.equal(actualChanged.deltas.overdue_tasks, 0)
  assert.equal(actualChanged.task_changes.length, 0)
  for (const method of [null, '', 'unavailable']) assert.equal(compare(snapshot({ coverage: { expected_progress_method: method } })).deltas.expected_progress, null)
})

test('planned date and criticality changes are baseline changes, not execution deltas', () => {
  for (const changed of [{ planned_start: '2026-10-02' }, { due_date: '2026-10-21' }, { is_critical: true }]) {
    const result = compare(snapshot({ tasks: [assessedTask(changed)] }))
    assert.ok(Object.entries(result.deltas).filter(([key]) => key !== 'high_risks').every(([, value]) => value === null))
    assert.equal(result.deltas.high_risks, 0)
    assert.equal(result.task_changes[0].change, 'baseline_changed')
    assert.equal(result.task_changes[0].progress_delta, null)
    assert.match(result.reasons.join(' '), /Planning baseline changed/)
  }
  for (const missing of [{ planned_start: null }, { due_date: null }, { is_critical: null }, { due_date: '2026-02-30' }]) {
    const result = compare(snapshot({ tasks: [assessedTask(missing)] }))
    assert.equal(result.deltas.actual_progress, null)
    assert.match(result.reasons.join(' '), /baseline metadata is unavailable/)
  }
})

test('changed supplied targets are baseline changes even with the same as_of and actual observations', () => {
  const old = freezeInput(previousSnapshot({ as_of: today, coverage: { expected_progress_method: 'supplied_target' } }))
  const current = freezeInput(snapshot({
    coverage: { expected_progress_method: 'supplied_target' },
    tasks: [assessedTask({ expected_progress: 80, variance: -60 })],
    metrics: { expected_progress: 80, progress_variance: -60 },
  }))
  const result = compare(current, old)
  for (const field of ['actual_progress', 'expected_progress', 'progress_variance', 'delayed_tasks', 'blocked_tasks', 'overdue_tasks']) assert.equal(result.deltas[field], null, field)
  assert.equal(result.deltas.high_risks, 0)
  assert.equal(result.task_changes[0].change, 'baseline_changed')
  assert.equal(result.task_changes[0].progress_delta, null)
  assert.match(result.reasons.join(' '), /Planning baseline changed.*supplied targets/)
  const trends = buildMonitoringTrends([{ ...old, as_of: '2026-10-08' }, current], project)
  assert.equal(trends.points[1].baseline_changed, true)
  assert.match(trends.points[1].label, /Baseline changed/)
})

test('unchanged supplied targets remain comparable without needing mixed source provenance', () => {
  const coverage = { expected_progress_method: 'supplied_target' }
  const result = compare(snapshot({ coverage }), previousSnapshot({ coverage }))
  assert.equal(result.deltas.expected_progress, 0)
  assert.equal(result.deltas.progress_variance, 0)
  assert.equal(result.deltas.delayed_tasks, 0)
  assert.deepEqual(result.task_changes, [])
  assert.ok(!result.reasons.some(reason => reason.includes('target provenance')))
})

test('recorded mixed target values and per-task method assignments identify baseline changes', () => {
  const coverage = { expected_progress_method: 'mixed' }
  const old = previousSnapshot({
    coverage, planning_context: mixedPlanning(),
    tasks: [assessedTask(), assessedTask({ task_id: 't2', expected_progress: 20, variance: 0, delayed: false })],
    metrics: { expected_progress: 35, progress_variance: -15 },
  })
  const cases = [
    { planning_context: mixedPlanning(80), tasks: [assessedTask({ expected_progress: 80, variance: -60 }), old.tasks[1]], metrics: { expected_progress: 50, progress_variance: -30 }, changed: ['t1'] },
    { planning_context: { tasks: [{ task_id: 't1', expected_progress: 80 }, { task_id: 't2' }] }, tasks: [assessedTask({ expected_progress: 80, variance: -60 }), old.tasks[1]], metrics: { expected_progress: 50, progress_variance: -30 }, changed: ['t1'] },
    { planning_context: { tasks: [{ task_id: 't1', expected_progress: null }, { task_id: 't2', expected_progress: 50 }] }, tasks: [assessedTask(), assessedTask({ task_id: 't2' })], metrics: { delayed_tasks: 2 }, changed: ['t1', 't2'] },
  ]
  for (const { changed, ...overrides } of cases) {
    const current = snapshot({ coverage, ...overrides })
    const result = compare(current, old)
    assert.equal(result.deltas.expected_progress, null)
    assert.equal(result.deltas.progress_variance, null)
    assert.equal(result.deltas.delayed_tasks, null)
    assert.equal(result.deltas.high_risks, 0)
    assert.deepEqual(result.task_changes.map(change => [change.task_id, change.change]), changed.map(id => [id, 'baseline_changed']))
    assert.equal(buildMonitoringTrends([old, current], project).points[1].baseline_changed, true)
  }
})

test('known mixed provenance permits calendar progression without inventing a target baseline change', () => {
  const coverage = { expected_progress_method: 'mixed' }
  const old = previousSnapshot({
    coverage, planning_context: mixedPlanning(),
    tasks: [assessedTask(), assessedTask({ task_id: 't2', expected_progress: 20, variance: 0, delayed: false })],
    metrics: { expected_progress: 35, progress_variance: -15 },
  })
  const current = snapshot({
    coverage, planning_context: mixedPlanning(),
    tasks: [assessedTask(), assessedTask({ task_id: 't2', expected_progress: 30, variance: -10 })],
    metrics: { expected_progress: 40, progress_variance: -20, delayed_tasks: 2 },
  })
  const result = compare(current, old)
  assert.equal(result.deltas.actual_progress, 0)
  assert.equal(result.deltas.expected_progress, 5)
  assert.equal(result.deltas.progress_variance, -5)
  assert.equal(result.deltas.delayed_tasks, 1)
  assert.deepEqual(result.task_changes, [])
  assert.equal(buildMonitoringTrends([old, current], project).points[1].baseline_changed, false)
  assert.ok(!result.reasons.some(reason => reason.includes('target provenance')))
})

test('unknown or inconsistent mixed provenance leaves only target-dependent eligibility unknown with caution', () => {
  const coverage = { expected_progress_method: 'mixed' }
  const old = previousSnapshot({ coverage, planning_context: mixedPlanning(), tasks: [assessedTask(), assessedTask({ task_id: 't2' })], metrics: { delayed_tasks: 2 } })
  const cases = [
    undefined,
    { tasks: [{ task_id: 't1', expected_progress: 50 }, { task_id: 't2' }] },
    { tasks: [{ task_id: 't1', expected_progress: 50 }, { task_id: 't2', expected_progress: undefined }] },
    ...['50', NaN, 40].map(expected_progress => mixedPlanning(expected_progress)),
    { tasks: [...mixedPlanning().tasks, { task_id: 't1', expected_progress: 60 }] },
    { tasks: [{ task_id: 't1', expected_progress: 50, project_id: 'foreign' }, mixedPlanning().tasks[1]] },
  ]
  for (const planning_context of cases) {
    const current = freezeInput(snapshot({
      coverage, planning_context,
      tasks: ['t1', 't2'].map(task_id => assessedTask({ task_id, actual_progress: 40, variance: -10 })),
      metrics: { actual_progress: 40, progress_variance: -10, delayed_tasks: 2 },
    }))
    for (const result of [compare(current, old), compare(current, { ...old, planning_context: undefined })]) {
      for (const field of ['expected_progress', 'progress_variance', 'delayed_tasks']) assert.equal(result.deltas[field], null, field)
      assert.equal(result.deltas.actual_progress, 20)
      for (const field of ['blocked_tasks', 'overdue_tasks', 'high_risks']) assert.equal(result.deltas[field], 0, field)
      assert.ok(result.task_changes.every(change => change.change === 'progress_changed' && change.progress_delta === 20))
      assert.match(result.reasons.join(' '), /target provenance metadata is unavailable.*eligibility is unknown/)
    }
    const trends = buildMonitoringTrends([old, current], project)
    assert.match(trends.points[1].label, /Expected target provenance unavailable/)
    assert.match(trends.limitations.join(' '), /target baseline comparison eligibility is unknown/)
    assert.equal(trends.points[1].expected_progress, 50, 'Recorded snapshot values remain distinct from comparison eligibility')
  }
})

test('dependency baselines are compared from report-owned Planning context when present', () => {
  const tasks = [assessedTask(), assessedTask({ task_id: 't2' })]
  const old = previousSnapshot({ tasks, planning_context: { schedule: [{ task_id: 't1', dependencies: [] }, { task_id: 't2', dependencies: [] }] } })
  const current = snapshot({ tasks, planning_context: { source_message: { data: { project_id: project, schedule: [{ task_id: 't1', dependencies: [] }, { task_id: 't2', dependencies: ['t1'] }] } } } })
  const result = compare(current, old)
  assert.equal(result.deltas.actual_progress, null)
  assert.deepEqual(result.task_changes.map(change => [change.task_id, change.change]), [['t2', 'baseline_changed']])
  const unavailable = compare(snapshot({ tasks }), old)
  assert.equal(unavailable.deltas.actual_progress, 0)
  assert.match(unavailable.reasons.join(' '), /Dependency metadata unavailable/)
  const reordered = [assessedTask({ dependency_ids: [] }), assessedTask({ task_id: 't2', dependency_ids: [] }), assessedTask({ task_id: 't3', dependency_ids: ['t1', 't2'] })]
  const same = compare(snapshot({ tasks: reordered.map(task => ({ ...task, dependency_ids: [...task.dependency_ids].reverse() })) }), previousSnapshot({ tasks: reordered }))
  assert.equal(same.deltas.actual_progress, 0)
  assert.ok(!same.reasons.some(reason => reason.includes('baseline changed')))
})

test('added and removed tasks are identified as baseline changes, not completed work', () => {
  const old = previousSnapshot({ tasks: [assessedTask({ task_id: 'removed', status: 'completed', actual_progress: 100 })] })
  const current = snapshot({ tasks: [assessedTask({ task_id: 'added', status: 'completed', actual_progress: 100 })] })
  const result = compare(current, old)
  assert.deepEqual(result.task_changes.map(change => [change.task_id, change.change, change.progress_delta]), [['added', 'added_to_baseline', null], ['removed', 'removed_from_baseline', null]])
  assert.equal(result.task_changes[0].from_status, null)
  assert.equal(result.task_changes[1].to_status, null)
  assert.equal(result.deltas.actual_progress, null)
  assert.match(result.reasons.join(' '), /not completed work/)
})

test('missing or stale status transitions are observation changes, not execution proof', () => {
  const done = snapshot({ tasks: [assessedTask({ status: 'completed', actual_progress: 100, completed_on: '2026-10-09', last_updated_on: '2026-10-09' })] })
  for (const status of ['missing', 'stale']) {
    const result = compare(done, previousSnapshot({ tasks: [assessedTask({ status, actual_progress: null, actual_is_stale: status === 'stale' })] }))
    assert.equal(result.task_changes[0].change, 'observation_changed')
    assert.equal(result.task_changes[0].progress_delta, null)
    assert.equal(result.deltas.actual_progress, null)
  }
  assert.equal(compare(done).task_changes[0].change, 'status_changed')
})

test('missing risks are no_longer_returned, never silently resolved', () => {
  const old = previousSnapshot({ risks: [sourceRisk('gone'), sourceRisk('closed'), sourceRisk('mitigated')], metrics: { high_risks: 3 } })
  const current = snapshot({ risks: [sourceRisk('closed', { status: 'closed' }), sourceRisk('mitigated', { status: 'mitigated' }), sourceRisk('new')], metrics: { high_risks: 1 } })
  const result = compare(current, old)
  assert.equal(result.deltas.high_risks, -2)
  assert.deepEqual(result.risk_changes.new.map(risk => risk.risk_id), ['new'])
  assert.deepEqual(result.risk_changes.no_longer_returned.map(risk => risk.risk_id), ['gone'])
  assert.deepEqual(result.risk_changes.status_changed, [
    { risk_id: 'closed', description: 'Source risk closed', from_status: 'open', to_status: 'closed' },
    { risk_id: 'mitigated', description: 'Source risk mitigated', from_status: 'open', to_status: 'mitigated' },
  ])
  assert.equal(result.risk_changes.resolved, undefined)
  assert.match(result.reasons.join(' '), /no longer returned are not resolved/)
})

test('risk counts and differences require both available fresh risk assessments', () => {
  for (const risk_status of ['not_supplied', 'stale', 'unavailable', null]) {
    const result = compare(snapshot({ coverage: { risk_status }, risks: [] }), previousSnapshot({ risks: [sourceRisk('old')] }))
    assert.equal(result.deltas.high_risks, null)
    assert.deepEqual(result.risk_changes, { new: [], no_longer_returned: [], status_changed: [], assessment_available: false })
  }
  for (const risk_observed_on of ['2026-10-10', '2026-02-30']) {
    assert.equal(compare(snapshot({ coverage: { risk_observed_on } })).deltas.high_risks, null)
  }
  assert.equal(compare(snapshot({ risks: undefined })).risk_changes.assessment_available, false)
  assert.equal(compare(snapshot({ risks: [sourceRisk('duplicate'), sourceRisk('duplicate')] })).risk_changes.assessment_available, false)
  assert.equal(compare().deltas.high_risks, 0)
})

test('trends return exact points sorted by as_of and latest verified generated_at with stable ID ties', () => {
  const earlier = previousSnapshot()
  const first = snapshot({ report_id: 'same-day-first', generated_at: '2026-10-09T09:00:00Z' })
  const latestA = snapshot({ report_id: 'same-day-a', generated_at: '2026-10-09T12:00:00Z' })
  const latestZ = snapshot({ report_id: 'same-day-z', generated_at: '2026-10-09T14:00:00+02:00' })
  const result = buildMonitoringTrends([first, latestZ, latestA, earlier], project)
  assert.deepEqual(result.points.map(point => point.report_id), ['previous', 'same-day-z'])
  assert.deepEqual(Object.keys(result.points[0]), ['report_id', 'as_of', 'generated_at', 'label', 'actual_progress', 'expected_progress', 'delayed_tasks', 'high_risks', 'coverage_label', 'baseline_changed'])
  assert.match(result.limitations.join(' '), /Limited history.*latest verified snapshot/)
  assert.deepEqual(buildMonitoringTrends([earlier, latestA, latestZ, first], project), result)
  assert.deepEqual(buildMonitoringTrends([], project).points, [])
})

test('backend microseconds and finer fractions order comparisons, trends and previous selection exactly', () => {
  const old = snapshot({ report_id: 'z-older', generated_at: '2026-10-09T12:00:00.123001Z' })
  const current = snapshot({ report_id: 'a-newer', generated_at: '2026-10-09T14:00:00.123999+02:00' })
  const newest = snapshot({ report_id: '0-newest', generated_at: '2026-10-09T12:00:00.123999001Z' })
  for (const [a, b] of [[current, old], [newest, current]]) {
    const result = compare(a, b)
    assert.equal(result.comparable, true)
    assert.ok(Object.values(result.deltas).every(value => value === 0))
    assert.deepEqual(selectPreviousMonitoringReport(a, [b], project), b)
    const trends = buildMonitoringTrends([a, b], project)
    assert.equal(trends.points[0].report_id, a.report_id)
    assert.equal(trends.points[0].generated_at, a.generated_at)
    assert.equal(csvRecords(reportCsv(a, project))[0].generated_at, a.generated_at)
    assert.doesNotThrow(() => JSON.stringify({ result, trends }))
  }
  assert.deepEqual(selectPreviousMonitoringReport(newest, [old, current], project), current)
  assert.equal(buildMonitoringTrends([newest, current, old], project).points[0].report_id, '0-newest')
  const equal = snapshot({ report_id: 'z-equal', generated_at: '2026-10-09T12:00:00.123999000Z' })
  assert.equal(compare(equal, current).comparable, false)
  assert.equal(selectPreviousMonitoringReport(current, [equal], project), null)
  assert.equal(buildMonitoringTrends([current, equal], project).points[0].report_id, 'z-equal', 'Only genuinely equal instants use the ID tie-break')
})

test('history deduplicates identical valid IDs and rejects conflicting IDs and foreign metadata', () => {
  const old = previousSnapshot()
  const reordered = Object.fromEntries(Object.entries(old).reverse())
  const good = buildMonitoringTrends([old, reordered], project)
  assert.equal(good.points.length, 1)
  assert.match(good.limitations.join(' '), /Identical duplicate.*deduplicated/)
  const bad = buildMonitoringTrends([old, previousSnapshot({ metrics: { actual_progress: 90 } }), snapshot({ project_id: 'foreign' }), snapshot({ as_of: null })], project)
  assert.deepEqual(bad.points, [])
  assert.match(bad.limitations.join(' '), /Conflicting duplicate.*rejected/)
  assert.match(bad.limitations.join(' '), /foreign-project report metadata was omitted/)
})

test('trends use only real dates and disclose unverifiable snapshot chronology', () => {
  const unknown = previousSnapshot({ generated_at: undefined })
  const result = buildMonitoringTrends([unknown], project)
  assert.equal(result.points[0].as_of, '2026-10-08')
  assert.equal(result.points[0].generated_at, null)
  assert.match(result.points[0].label, /Generated timestamp unavailable/)
  assert.match(result.limitations.join(' '), /unknown timestamps cannot establish/)
  const ambiguous = buildMonitoringTrends([unknown, previousSnapshot({ report_id: 'other', generated_at: 'not-a-date' })], project)
  assert.deepEqual(ambiguous.points, [])
  const dated = buildMonitoringTrends([unknown, previousSnapshot({ report_id: 'verified' })], project)
  assert.equal(dated.points[0].report_id, 'verified')
})

test('trend bars label baseline/method changes, gaps, unknown metadata and known risk zero', () => {
  const changed = snapshot({ tasks: [assessedTask({ due_date: '2026-10-21' })], coverage: { expected_progress_method: 'mixed', actual_progress_method: 'weighted_average' } })
  const result = buildMonitoringTrends([changed, previousSnapshot()], project)
  assert.equal(result.points[1].baseline_changed, true)
  assert.match(result.points[1].label, /Baseline changed/)
  assert.match(result.points[1].label, /Expected method changed/)
  assert.match(result.points[1].label, /Actual method changed/)
  assert.equal(result.points[1].actual_progress, null)
  assert.equal(result.points[1].high_risks, 0)
  const missingMetadata = buildMonitoringTrends([snapshot({ tasks: [assessedTask({ planned_start: null })] })], project)
  assert.match(missingMetadata.points[0].label, /Baseline metadata unavailable/)
  for (const risk_status of ['stale', 'unavailable', 'not_supplied']) assert.equal(buildMonitoringTrends([snapshot({ coverage: { risk_status } })], project).points[0].high_risks, null)
  const zero = buildMonitoringTrends([snapshot({ tasks: [assessedTask({ actual_progress: 0 })], metrics: { actual_progress: 0, expected_progress: 0, delayed_tasks: 0 } })], project).points[0]
  assert.equal(zero.actual_progress, 0)
  assert.equal(zero.expected_progress, 0)
  assert.equal(zero.delayed_tasks, 0)
})

test('previous selection picks nearest earlier as_of/generated_at and excludes unsafe candidates', () => {
  const old = previousSnapshot()
  const sameDay = snapshot({ report_id: 'same-day', generated_at: '2026-10-09T11:00:00Z' })
  const candidates = [old, sameDay, snapshot(), snapshot({ report_id: 'future-asof', as_of: '2026-10-10', generated_at: '2026-10-08T13:00:00Z' }), snapshot({ report_id: 'future-generated', as_of: '2026-10-08', generated_at: '2026-10-10T12:00:00Z' }), previousSnapshot({ report_id: 'unknown', generated_at: null }), previousSnapshot({ project_id: 'foreign' })]
  assert.deepEqual(selectPreviousMonitoringReport(snapshot(), candidates, project), sameDay)
  assert.deepEqual(selectPreviousMonitoringReport(snapshot(), [old], project), old)
  assert.equal(selectPreviousMonitoringReport(snapshot(), [snapshot()], project), null)
  assert.equal(selectPreviousMonitoringReport(snapshot({ generated_at: undefined }), candidates, project), null)
  assert.equal(selectPreviousMonitoringReport(snapshot(), [old, previousSnapshot({ metrics: { actual_progress: 90 } })], project), null)
  assert.equal(selectPreviousMonitoringReport(snapshot(), [], project), null)
  assert.equal(selectPreviousMonitoringReport(snapshot(), [previousSnapshot({ generated_at: '2026-10-08T24:00:00Z' })], project), null)
})

test('alerts are evidence-bound, deterministic, deduplicated and source-recommendation-only', () => {
  const warning = { code: 'DELAYED_TASK', severity: 'warning', message: 'Source confirms delay', task_ids: ['t1'], source_refs: ['task:t1', 'task:foreign', 'javascript:bad', 'made-up'] }
  const high = { code: 'HIGH_RISK', severity: 'warning', message: 'Backend high-risk evidence', task_ids: ['t1'], source_refs: ['risk:high'] }
  const evidence = { code: 'PLANNING_WARNING', severity: 'info', message: 'Recorded evidence', task_ids: ['foreign'], source_refs: ['document:doc1', 'document:missing', 'javascript:bad'] }
  const value = snapshot({
    warnings: [warning, warning, high, evidence], retrieved_evidence: [{ document_id: 'doc1', source: 'planning-document' }],
    risks: [sourceRisk('high', { recommended_action: 'Source says review capacity' }), sourceRisk('critical', { level: 'critical' }), sourceRisk('closed', { status: 'closed' }), sourceRisk('low', { level: 'low' })],
    suggested_actions: ['Do not automatically assign this recommendation'],
  })
  const alerts = monitoringAlerts(value, project)
  // A warning claiming a foreign task source is not execution evidence.
  assert.equal(alerts.length, 3)
  assert.equal(alerts[0].severity, 'critical')
  assert.deepEqual(alerts.map(alert => alert.id), monitoringAlerts({ ...value, warnings: [...value.warnings].reverse(), risks: [...value.risks].reverse() }, project).map(alert => alert.id))
  const riskAlert = alerts.find(alert => alert.id === 'risk:current:high')
  assert.equal(riskAlert.message, high.message)
  assert.equal(riskAlert.recommended_action, 'Source says review capacity')
  assert.deepEqual(riskAlert.source_refs, ['risk:high'])
  const evidenceAlert = alerts.find(alert => alert.message === evidence.message)
  assert.deepEqual(evidenceAlert.task_ids, [])
  assert.deepEqual(evidenceAlert.source_refs, ['document:doc1'])
  assert.ok(alerts.filter(alert => alert.id !== riskAlert.id).every(alert => !Object.hasOwn(alert, 'recommended_action')))
  const validWarning = { ...warning, source_refs: ['task:t1'] }
  const duplicates = monitoringAlerts(snapshot({ warnings: [validWarning, { ...validWarning, severity: 'critical' }, validWarning] }), project)
  assert.equal(duplicates.length, 1)
  assert.equal(duplicates[0].severity, 'critical')
  assert.deepEqual(Object.keys(duplicates[0]), ['id', 'severity', 'title', 'message', 'task_ids', 'source_refs'])
})

test('ambiguous risk source identities never mix warning text, recommendations or task associations', () => {
  const risks = [
    sourceRisk('a', { source: 'risk:b', recommended_action: 'Recommendation A' }),
    sourceRisk('b', { source: 'b.pdf', task_ids: ['t2'], recommended_action: 'Recommendation B' }),
  ]
  const warnings = risks.map(risk => ({ code: 'HIGH_RISK', severity: 'warning', message: `Open high risk ${risk.risk_id}: ${risk.description}`, task_ids: risk.task_ids, source_refs: [`risk:${risk.risk_id}`, risk.source] }))
  warnings[0].recommended_action = 'Ambiguous warning action must not override either source'
  const value = freezeInput(snapshot({ risks, warnings, tasks: [assessedTask(), assessedTask({ task_id: 't2' })] }))
  const alerts = monitoringAlerts(value, project)
  assert.equal(alerts.length, 2)
  assert.equal(alerts[0].message, risks[0].description)
  assert.equal(alerts[0].recommended_action, 'Recommendation A')
  assert.deepEqual(alerts[0].task_ids, ['t1'])
  assert.equal(alerts[1].message, warnings[1].message)
  assert.equal(alerts[1].recommended_action, 'Recommendation B')
  assert.deepEqual(alerts[1].task_ids, ['t2'])
  assert.deepEqual(alerts[1].source_refs, ['b.pdf', 'risk:b'])
  assert.deepEqual(monitoringAlerts({ ...value, risks: [...risks].reverse(), warnings: [...warnings].reverse() }, project), alerts)
})

test('risk identity ambiguity includes returned closed risks, not just open high risks', () => {
  const risks = [sourceRisk('a', { source: 'risk:b', recommended_action: 'Recommendation A' }), sourceRisk('b', { status: 'closed' })]
  const warnings = [{ code: 'HIGH_RISK', severity: 'warning', message: 'Ambiguous risk warning', source_refs: ['risk:a', 'risk:b'], recommended_action: 'Unbound action' }]
  const alerts = monitoringAlerts(snapshot({ risks, warnings }), project)
  assert.equal(alerts.length, 1)
  assert.equal(alerts[0].message, risks[0].description)
  assert.equal(alerts[0].recommended_action, risks[0].recommended_action)
})

test('alerts never infer overdue/delay from a deadline or missing/stale actuals', () => {
  const warnings = [
    { code: 'OVERDUE_TASK', severity: 'critical', message: 'Task overdue', task_ids: ['t1'], source_refs: ['task:t1'] },
    { code: 'DELAYED_TASK', severity: 'warning', message: 'Task delayed', task_ids: ['t1'], source_refs: ['task:t1'] },
    { code: 'BLOCKED_TASK', severity: 'warning', message: 'Task blocked', task_ids: ['t1'], source_refs: ['task:t1'] },
  ]
  for (const changed of [{ status: 'missing', actual_progress: null }, { status: 'stale', actual_is_stale: true }, { actual_progress: '20%' }]) {
    assert.deepEqual(monitoringAlerts(snapshot({ warnings, tasks: [assessedTask({ ...changed, due_date: '2026-10-01', overdue_days: 8, delayed: true })] }), project), [])
  }
  assert.deepEqual(monitoringAlerts(snapshot({ tasks: [assessedTask({ due_date: '2026-10-01' })] }), project), [])
  assert.deepEqual(monitoringAlerts(snapshot({ warnings: [warnings[0]] }), project), [])
  assert.deepEqual(monitoringAlerts(snapshot({ warnings, coverage: null }), project), [])
  const confirmed = monitoringAlerts(snapshot({ warnings, tasks: [assessedTask({ status: 'blocked', overdue_days: 2 })] }), project)
  assert.equal(confirmed.length, 3)
  const deadlineWarning = { code: 'PLANNED_DEADLINE_PASSED', severity: 'warning', message: 'Planned deadline passed; completion is unknown', task_ids: ['t1'], source_refs: ['task:t1'] }
  const unknown = monitoringAlerts(snapshot({ warnings: [deadlineWarning], tasks: [assessedTask({ status: 'missing', actual_progress: null })] }), project)
  assert.equal(unknown.length, 1)
  assert.equal(unknown[0].message, deadlineWarning.message)
})

test('high-risk alerts require fresh available source risks and never dispatch', () => {
  const highWarning = { code: 'HIGH_RISK', severity: 'warning', message: 'High risk', source_refs: ['risk:high'] }
  for (const risk_status of ['stale', 'unavailable', 'not_supplied', null]) {
    assert.deepEqual(monitoringAlerts(snapshot({ coverage: { risk_status }, risks: [sourceRisk('high')], warnings: [highWarning] }), project), [])
  }
  for (const risks of [undefined, [], [sourceRisk('high', { status: 'closed' })], [sourceRisk('high', { status: 'mitigated' })], [sourceRisk('high', { level: 'medium' })]]) {
    assert.deepEqual(monitoringAlerts(snapshot({ risks, warnings: [highWarning] }), project), [])
  }
  const riskUnavailable = { code: 'RISK_UNAVAILABLE', severity: 'warning', message: 'Current risk coverage unknown, not zero', source_refs: ['risk:provider'] }
  const alert = monitoringAlerts(snapshot({ coverage: { risk_status: 'unavailable' }, warnings: [riskUnavailable] }), project)[0]
  assert.deepEqual(alert.source_refs, ['risk:provider'])
  assert.equal(alert.recommended_action, undefined)
})

test('alerts redact JSON-encoded credentials and reject unsafe source links', () => {
  const message = JSON.stringify({ token: 'credential-value', evidence: 'Keep warning' })
  const value = snapshot({ warnings: [{ code: 'SOURCE_WARNING', severity: 'info', message, source_refs: ['javascript:alert(1)', 'document:doc1'] }], retrieved_evidence: [{ document_id: 'doc1', source: 'javascript:alert(1)' }] })
  const alert = monitoringAlerts(value, project)[0]
  assert.deepEqual(JSON.parse(alert.message), { evidence: 'Keep warning' })
  assert.deepEqual(alert.source_refs, ['document:doc1'])
  assert.equal(value.warnings[0].message, message)
})

test('CSV quotes commas, quotes, Unicode and embedded newlines with consistent columns', () => {
  const title = 'Design, "review"\r\nActual evidence — café'
  const value = snapshot({ tasks: [assessedTask({ title, status: 'completed', actual_progress: 100, completed_on: '2026-10-08', last_updated_on: '2026-10-08' })] })
  const csv = reportCsv(value, project)
  assert.ok(csv.includes('"Design, ""review""\r\nActual evidence — café"'))
  assert.ok(csv.endsWith('\r\n'))
  const [metadata, task] = csvRecords(csv)
  assert.equal(metadata.row_type, 'report')
  assert.equal(task.row_type, 'task')
  assert.equal(task.title, title)
  assert.equal(task.actual_start, '2026-10-02')
  assert.equal(task.actual_end, '2026-10-08')
  assert.equal(task.observed_on, '2026-10-08')
  assert.equal(task.planned_start, '2026-10-01')
  assert.equal(task.due_date, '2026-10-20')
  assert.equal(task.coverage_label, metadata.coverage_label)
  assert.equal(task.report_progress_variance, '-30')
  assert.ok(csv.includes(',-30,'), 'Typed negative numbers remain numeric cells')
})

test('CSV protects all formula prefixes and leading controls without changing typed numbers', () => {
  for (const title of ['=HYPERLINK("bad")', '+1+2', '-42', '@SUM(A1)', '\tformula', '\rformula', '\nformula', '\u0001formula', '  =1', ' \t=1', '\u200b=1']) {
    const task = csvRecords(reportCsv(snapshot({ tasks: [assessedTask({ title })] }), project))[1]
    assert.equal(task.title, `'${title}`)
    assert.equal(task.task_progress_variance, '-30')
  }
  const plain = csvRecords(reportCsv(snapshot({ tasks: [assessedTask({ title: "O'Brien" })] }), project))[1]
  assert.equal(plain.title, "O'Brien")
})

test('CSV leaves null/legacy metadata and unknown numeric values blank, preserving known zero', () => {
  const legacy = snapshot({ generated_at: null, coverage: null, tasks: [assessedTask({ actual_start: null, completed_on: null, last_updated_on: null, actual_progress: null, expected_progress: '50%', variance: null, is_critical: null, actual_is_stale: null })], metrics: { actual_progress: null, expected_progress: '50%', progress_variance: null, high_risks: 0 } })
  const [metadata, task] = csvRecords(reportCsv(legacy, project))
  for (const field of ['generated_at', 'planning_status', 'actual_status', 'total_tasks', 'fresh_actual_tasks', 'report_actual_progress', 'report_expected_progress', 'report_high_risks', 'actual_observed_on']) assert.equal(metadata[field], '', field)
  for (const field of ['actual_start', 'actual_end', 'observed_on', 'task_actual_progress', 'task_expected_progress', 'task_progress_variance', 'is_critical', 'actual_is_stale', 'overdue_days']) assert.equal(task[field], '', field)
  const zero = csvRecords(reportCsv(snapshot({ metrics: { actual_progress: 0, expected_progress: 0, delayed_tasks: 0 }, tasks: [assessedTask({ actual_progress: 0, expected_progress: 0, variance: 0, delayed: false })] }), project))[1]
  assert.equal(zero.report_actual_progress, '0')
  assert.equal(zero.report_high_risks, '0')
  assert.equal(zero.task_actual_progress, '0')
  assert.equal(zero.task_progress_variance, '0')
  assert.equal(zero.delayed, 'false')
  assert.equal(csvRecords(reportCsv(snapshot({ tasks: [] }), project)).length, 1)
})

test('CSV exports assessment-owned dates and redacted data, never live-baseline or auth fields', () => {
  const value = snapshot({
    access_token: 'credential-value', requested_by: '=PRIVATE()',
    actual_context: { source_payload: { project_id: project, accessToken: 'credential-value' } },
    planning_context: { tasks: [{ task_id: 't1', start_date: '2025-01-01', due_date: '2025-02-01' }], apiKey: 'credential-value' },
    tasks: [assessedTask({ title: JSON.stringify({ password: 'credential-value', note: 'Keep title' }), completed_on: null, actual_end: '2026-10-08' })],
  })
  const csv = reportCsv(value, project)
  assert.ok(!csv.includes('credential-value'))
  assert.ok(!csv.includes('PRIVATE'))
  assert.ok(!csv.includes('access_token'))
  const task = csvRecords(csv)[1]
  assert.deepEqual(JSON.parse(task.title), { note: 'Keep title' })
  assert.equal(task.planned_start, '2026-10-01')
  assert.equal(task.due_date, '2026-10-20')
  assert.equal(task.actual_end, '2026-10-08')
})

test('all new helpers leave deeply frozen reports, arrays and source evidence unchanged', () => {
  const current = freezeInput(snapshot({ risks: [sourceRisk('high', { accessToken: 'credential-value' })], warnings: [{ code: 'SOURCE_WARNING', severity: 'info', message: 'Source text', source_refs: ['risk:high'] }] }))
  const old = freezeInput(previousSnapshot({ risks: [sourceRisk('old')] }))
  const reports = freezeInput([current, old])
  const original = JSON.stringify(reports)
  const compared = compareMonitoringReports(current, old, project)
  buildMonitoringTrends(reports, project)
  monitoringAlerts(current, project)
  reportCsv(current, project)
  const selected = selectPreviousMonitoringReport(current, reports, project)
  assert.equal(JSON.stringify(reports), original)
  assert.notEqual(selected, old)
  assert.ok(!JSON.stringify(compared).includes('credential-value'))
  assert.equal(current.risks[0].accessToken, 'credential-value')
})

test('invalid actual chronology and inconsistent completion cannot establish fresh execution', () => {
  for (const changes of [
    { actual_start: '2026-02-30' }, { actual_start: '2026-10-10' },
    { actual_start: '2026-10-08', last_updated_on: '2026-10-07' },
    { status: 'completed', actual_progress: 100, completed_on: '2026-10-08', last_updated_on: '2026-10-07' },
    { status: 'completed', actual_progress: 100, completed_on: '2026-10-01', actual_start: '2026-10-02' },
    { status: 'completed', actual_progress: 100, completed_on: null },
    { status: 'in_progress', actual_progress: 100 },
    { status: 'not_started', actual_progress: 20 },
    { status: 'not_started', actual_progress: 0, actual_start: '2026-10-02' },
  ]) {
    const value = snapshot({ tasks: [assessedTask(changes)] })
    assert.equal(compare(value).deltas.actual_progress, null)
    assert.equal(buildMonitoringTrends([value], project).points[0].actual_progress, null)
    assert.equal(csvRecords(reportCsv(value, project))[1].task_actual_progress, '')
    assert.deepEqual(monitoringAlerts({ ...value, warnings: [{ code: 'DELAYED_TASK', severity: 'warning', message: 'Delay', task_ids: ['t1'], source_refs: ['task:t1'] }] }, project), [])
  }
})

test('reversed baseline dates are unavailable and invalid CSV dates remain blank', () => {
  const reversed = snapshot({ tasks: [assessedTask({ planned_start: '2026-10-21', due_date: '2026-10-20' })] })
  assert.equal(compare(reversed).deltas.actual_progress, null)
  assert.match(compare(reversed).reasons.join(' '), /baseline metadata is unavailable/)
  const invalid = snapshot({ tasks: [assessedTask({ planned_start: '2026-02-30', actual_start: 'not-a-date', last_updated_on: '2026-13-01' })], generated_at: '2026-10-09T12:00:00' })
  const task = csvRecords(reportCsv(invalid, project))[1]
  assert.equal(task.planned_start, '')
  assert.equal(task.actual_start, '')
  assert.equal(task.observed_on, '')
  assert.equal(task.generated_at, '')
})
