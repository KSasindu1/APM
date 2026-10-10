import test from 'node:test'
import assert from 'node:assert/strict'
import { createMonitoringApi, MONITORING_API_URL, MonitoringApiError, resolveMonitoringApiUrl, validateMonitoringSession, validateObservation } from './monitoringApi.js'

const project = 'authorized-project'
function report(overrides = {}) {
  return { report_id: 'report-1', request_id: 'request-1', project_id: project, as_of: '2026-10-08', status: 'AT_RISK', metrics: { actual_progress: null }, tasks: [], warnings: [], limitations: [], planning_feedback: [], ...overrides }
}
function actualProgress(overrides = {}) {
  return { project_id: project, tasks: [{ task_id: 'task-1', status: 'IN_PROGRESS', progress_percentage: 35 }], ...overrides }
}
function observationBody(overrides = {}) {
  return { request_id: 'observation-request-1', source: 'manual', observed_on: '2026-10-08', actual_progress: actualProgress(), ...overrides }
}
function receipt(overrides = {}) {
  return { observation_id: 'observation-1', request_id: 'observation-request-1', project_id: project, source: 'manual', observed_on: '2026-10-08', accepted_at: '2026-10-08T14:12:00+00:00', submitted_by: 'server-principal', actual_progress: actualProgress(), persisted: false, limitations: ['Source is a caller-supplied label, not verified provenance.'], ...overrides }
}
const malformed = { name: 'MonitoringApiError', status: 0, kind: 'response', message: 'Monitoring returned malformed or mismatched data. The response was not used.' }
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
function client(fetchImpl, options = {}) {
  return createMonitoringApi({ baseUrl: 'https://monitoring.example.test/', token: 'scoped-test-token', fetchImpl, ...options })
}

test('session contract accepts UUID/adapter subjects and rejects invalid, duplicate or ambiguous grants', () => {
  const data = { subject: '1b5e7946-0f72-4a41-9c5e-4184c817790b', project_ids: [project], permissions: ['monitoring:read', 'monitoring:generate'] }
  for (const subject of [data.subject, 'bootstrap-admin', 'legacy-reader']) {
    assert.deepEqual(validateMonitoringSession({ ...data, subject }), { ...data, subject })
  }
  assert.deepEqual(validateMonitoringSession({ ...data, project_ids: [], permissions: [] }), { ...data, project_ids: [], permissions: [] })
  for (const response of [null, [], {},
    ...['', ' ', ' padded', 'line\nbreak', 'identity😀', 'x'.repeat(101), 1].map(subject => ({ ...data, subject })),
    ...[null, 'not-a-list', [''], [' '], ['foreign/project'], [1], [project, project]].map(project_ids => ({ ...data, project_ids })),
    ...[null, 'monitoring:read', ['admin'], ['monitoring:read', 'monitoring:read']].map(permissions => ({ ...data, permissions })),
  ]) assert.throws(() => validateMonitoringSession(response), malformed)
  const safe = validateMonitoringSession({ ...data, token: 'private-token', role: 'admin' })
  assert.deepEqual(Object.keys(safe), ['subject', 'project_ids', 'permissions'])
  assert.ok(Object.isFrozen(safe) && Object.isFrozen(safe.project_ids) && Object.isFrozen(safe.permissions))
  data.project_ids.push('later-injected-project')
  assert.deepEqual(safe.project_ids, [project])
})

test('abort bounds transports and body readers that ignore cancellation', async () => {
  for (const bodyRead of [false, true]) {
    const controller = new AbortController()
    let signal
    const pending = client(async (_url, init) => {
      signal = init.signal
      if (!bodyRead) return new Promise(() => {})
      return { ok: true, json: () => new Promise(() => {}) }
    }).session(controller.signal)
    controller.abort()
    await assert.rejects(pending, error => error === controller.signal.reason)
    assert.equal(signal.aborted, true)
  }
})

test('timeout bounds transports and JSON parsing even if abort is ignored', async () => {
  for (const bodyRead of [false, true]) {
    const api = client(async () => bodyRead ? { ok: true, json: () => new Promise(() => {}) } : new Promise(() => {}), { timeoutMs: 5 })
    await assert.rejects(api.session(), { name: 'MonitoringApiError', status: 0, kind: 'timeout' })
  }
})

test('late JSON from a cancelled older session cannot replace a newer grant', async () => {
  let finishBody
  const controller = new AbortController()
  const old = client(async () => ({ ok: true, json: () => new Promise(resolve => { finishBody = resolve }) })).session(controller.signal)
  await new Promise(resolve => queueMicrotask(resolve))
  controller.abort()
  await assert.rejects(old, error => error === controller.signal.reason)
  const current = await client(async () => json({ subject: 'new-account', project_ids: ['new-project'], permissions: ['monitoring:read'] })).session()
  finishBody({ subject: 'old-account', project_ids: [project], permissions: ['monitoring:read'] })
  assert.deepEqual(current.project_ids, ['new-project'])
})

test('error kind defaults preserve the existing message and status constructor', () => {
  for (const status of [0, 401, 403, 503]) {
    const error = new MonitoringApiError('Safe message', status)
    assert.ok(error instanceof Error)
    assert.equal(error.name, 'MonitoringApiError')
    assert.equal(error.message, 'Safe message')
    assert.equal(error.status, status)
    assert.equal(error.kind, status > 0 ? 'http' : 'response')
  }
  const error = new MonitoringApiError('Safe message')
  assert.equal(error.status, 0)
  assert.equal(error.kind, 'response')
})

test('explicit error kinds are supported and unknown kinds fall back to response', () => {
  for (const kind of ['http', 'network', 'timeout', 'response', 'configuration']) {
    const error = new MonitoringApiError('Safe message', 0, kind)
    assert.equal(error.kind, kind)
    assert.equal(error.status, 0)
    assert.equal(error.message, 'Safe message')
  }
  for (const kind of ['unknown', '', null, 1]) {
    const error = new MonitoringApiError('Safe message', 503, kind)
    assert.equal(error.kind, 'response')
    assert.equal(error.status, 503)
  }
})

test('default URL uses the shared APM backend without a browser location', () => {
  assert.equal(MONITORING_API_URL, 'http://127.0.0.1:8000')
  assert.equal(resolveMonitoringApiUrl(), 'http://127.0.0.1:8000')
})

for (const origin of ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://[::1]:5173']) {
  test(`browser origin ${origin} routes public health through the same-origin proxy`, async () => {
    const baseUrl = resolveMonitoringApiUrl({ origin })
    assert.equal(baseUrl, origin)
    assert.equal(resolveMonitoringApiUrl({ configuredUrl: '', origin }), origin)
    const api = createMonitoringApi({ baseUrl, fetchImpl: async (url, init) => {
      assert.equal(url, `${origin}/api/monitoring/health`)
      assert.equal(init.headers.Authorization, undefined)
      assert.equal(init.credentials, 'omit')
      return json({ status: 'configuration_required', capabilities: { auth_configured: false, planning_configured: true, risk_configured: true, storage_configured: false }, live_sources_verified: false })
    } })
    assert.equal((await api.health()).status, 'configuration_required')
  })
}

test('explicit HTTPS configuration takes priority over the browser origin', async () => {
  const configuredUrl = 'https://monitoring.example.test'
  for (const origin of ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://[::1]:5173', undefined]) {
    assert.equal(resolveMonitoringApiUrl({ configuredUrl, origin }), configuredUrl)
  }
  const api = createMonitoringApi({
    baseUrl: resolveMonitoringApiUrl({ configuredUrl, origin: 'http://[::1]:5173' }),
    fetchImpl: async url => {
      assert.equal(url, `${configuredUrl}/api/monitoring/health`)
      return json({ status: 'ready', capabilities: { auth_configured: true, planning_configured: true, risk_configured: true, storage_configured: false }, live_sources_verified: false })
    },
  })
  assert.equal((await api.health()).status, 'ready')
})

test('unsafe explicit configuration is rejected rather than replaced with the origin', () => {
  for (const configuredUrl of ['not a URL', 'https://user:password@example.test', 'https://example.test?token=secret', 'http://monitoring.example.test']) {
    const baseUrl = resolveMonitoringApiUrl({ configuredUrl, origin: 'http://localhost:5173' })
    assert.equal(baseUrl, configuredUrl)
    assert.throws(() => createMonitoringApi({ baseUrl }), { name: 'MonitoringApiError', status: 0, kind: 'configuration', message: 'The configured Monitoring API URL is invalid.' })
  }
})

test('default client reaches Monitoring on the shared backend', async () => {
  const api = createMonitoringApi({ fetchImpl: async url => {
    assert.equal(url, 'http://127.0.0.1:8000/api/monitoring/health')
    return json({ status: 'configuration_required', capabilities: { auth_configured: false, planning_configured: true, risk_configured: true, storage_configured: false }, live_sources_verified: false })
  } })
  const result = await api.health()
  assert.equal(result.capabilities.planning_configured, true)
  assert.equal(result.capabilities.risk_configured, true)
})

test('health is public and does not attach a token or cookies', async () => {
  let call
  const api = client(async (url, init) => {
    call = { url, init }
    return json({ agent: 'monitoring', status: 'configuration_required', capabilities: { auth_configured: false, planning_configured: false, risk_configured: false, storage_configured: false }, live_sources_verified: false })
  })
  const result = await api.health()
  assert.equal(result.live_sources_verified, false)
  assert.equal(call.url, 'https://monitoring.example.test/api/monitoring/health')
  assert.equal(call.init.headers.Authorization, undefined)
  assert.equal(call.init.credentials, 'omit')
  assert.equal(call.init.cache, 'no-store')
  assert.equal(call.init.redirect, 'error')
  assert.equal(call.init.referrerPolicy, 'no-referrer')
})

test('standalone opaque-token session transport remains callable with bearer only in the header', async () => {
  const api = client(async (url, init) => {
    assert.equal(init.headers.Authorization, 'Bearer scoped-test-token')
    assert.equal(init.headers.Accept, 'application/json')
    assert.ok(!url.includes('scoped-test-token'))
    assert.equal(init.body, undefined)
    return json({ subject: 'reader', project_ids: [project], permissions: ['monitoring:read'] })
  })
  assert.deepEqual((await api.session()).project_ids, [project])
})

test('unconfigured or unsafe tokens do not initiate private requests', async () => {
  let calls = 0
  for (const token of ['', 'bad\r\ntoken', null, 123]) {
    await assert.rejects(client(async () => { calls += 1 }, { token }).session(), error => error.status === 401 && error.kind === 'http')
  }
  assert.equal(calls, 0)
})

test('project and report path segments are encoded and history limit is fixed', async () => {
  const id = 'project /?&'
  const rid = 'report /?&'
  const urls = []
  const api = client(async url => {
    urls.push(url)
    if (url.endsWith('/baseline')) return json({ project_id: id, fetched_on: '2026-10-08', tasks: [] })
    if (url.endsWith('?limit=20')) return json([report({ project_id: id })])
    return json(report({ project_id: id, report_id: rid }))
  })
  await api.baseline(id)
  await api.history(id)
  await api.report(id, rid)
  assert.deepEqual(urls, [
    'https://monitoring.example.test/api/monitoring/projects/project%20%2F%3F%26/baseline',
    'https://monitoring.example.test/api/monitoring/projects/project%20%2F%3F%26/reports?limit=20',
    'https://monitoring.example.test/api/monitoring/projects/project%20%2F%3F%26/reports/report%20%2F%3F%26',
  ])
})

test('generation uses JSON POST and validates correlation and report date', async () => {
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08', include_risks: true, save_report: false }
  const result = await client(async (url, init) => {
    assert.equal(url, 'https://monitoring.example.test/api/monitoring/reports')
    assert.equal(init.method, 'POST')
    assert.equal(init.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(init.body), body)
    assert.ok(!init.body.includes('scoped-test-token'))
    return json(report())
  }).generate(body)
  assert.equal(result.metrics.actual_progress, null)
})

for (const status of [401, 403, 404, 422, 502, 503, 504]) {
  test(`HTTP ${status} is readable without reflecting private HTML or detail`, async () => {
    const api = client(async () => new Response('<html>private-provider-secret</html>', { status }))
    await assert.rejects(api.session(), error => error instanceof MonitoringApiError && error.status === status && error.kind === 'http' && error.message.length > 20 && !error.message.includes('private-provider-secret') && !error.message.includes('<html>'))
  })
}

test('missing-auth HTTP 503 is not classified as a network or configuration error', async () => {
  const response = json({ detail: 'Monitoring authentication is not configured', input: 'private-value' }, 503)
  let bodyRead = false
  response.json = async () => { bodyRead = true; throw new Error('private-value') }
  await assert.rejects(client(async () => response).session(), {
    name: 'MonitoringApiError', status: 503, kind: 'http',
    message: 'Monitoring or a required source is not configured or currently unavailable.',
  })
  assert.equal(bodyRead, false)
})

test('JSON error details are not echoed either', async () => {
  await assert.rejects(client(async () => json({ detail: [{ input: 'private-value', msg: 'private-value' }] }, 422)).session(), error => error.status === 422 && error.kind === 'http' && !error.message.includes('private-value'))
})

test('successful HTTP with malformed JSON is rejected safely as a response error', async () => {
  for (const body of ['{invalid', '<html>private-provider-secret</html>']) {
    await assert.rejects(client(async () => new Response(body)).session(), {
      name: 'MonitoringApiError', status: 0, kind: 'response',
      message: 'Monitoring returned invalid JSON. The response was not used.',
    })
  }
})

test('unexpected response processing failures are safe response errors, not network errors', async () => {
  await assert.rejects(client(async () => undefined).session(), {
    name: 'MonitoringApiError', status: 0, kind: 'response',
    message: 'Monitoring returned malformed or mismatched data. The response was not used.',
  })
})

test('malformed session and malformed health are rejected', async () => {
  await assert.rejects(client(async () => json({ subject: 'reader', project_ids: 'not-a-list', permissions: [] })).session(), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /malformed/ })
  await assert.rejects(client(async () => json({ status: 'ready', capabilities: {}, live_sources_verified: false })).health(), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /malformed/ })
})

test('baseline project mismatch and duplicate task IDs are rejected', async () => {
  await assert.rejects(client(async () => json({ project_id: 'other', tasks: [], fetched_on: '2026-10-08' })).baseline(project), /mismatched/)
  await assert.rejects(client(async () => json({ project_id: project, tasks: [{ task_id: 't1', title: 'Task' }, { task_id: 't1', title: 'Duplicate' }], fetched_on: '2026-10-08' })).baseline(project), /malformed/)
})

test('history must be a list of full reports belonging to the requested project', async () => {
  await assert.rejects(client(async () => json({ reports: [] })).history(project), /malformed/)
  await assert.rejects(client(async () => json([report({ project_id: 'other' })])).history(project), /mismatched/)
})

test('report source contexts and evidence must stay bound to the authorized project on every route', async () => {
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08' }
  const foreign = { project_id: 'foreign-project' }
  const overrides = [
    { planning_context: foreign }, { planning_context: { data: foreign } },
    { planning_context: { source_message: foreign } }, { planning_context: { source_message: { data: foreign } } },
    { planning_context: { source_payload: foreign } }, { planning_context: { source_payload: { data: foreign } } },
    { actual_context: foreign }, { actual_context: { source_payload: foreign } },
    { actual_context: { observation_receipt: foreign } },
    { actual_context: { observation_receipt: { actual_progress: foreign } } },
    { tasks: [{ task_id: 'task-1', title: 'Task', status: 'missing', ...foreign }] },
    { risks: [{ risk_id: 'risk-1', description: 'Risk', level: 'HIGH', ...foreign }] },
    { milestones: [{ milestone_id: 'milestone-1', title: 'Milestone', status: 'unknown', ...foreign }] },
    ...['retrieved_evidence', 'risk_evidence_used', 'risk_knowledge_sources'].flatMap(field => [
      { [field]: [foreign] }, { [field]: [{ project_id: null }] }, { [field]: {} },
    ]),
  ]
  for (const override of overrides) {
    const data = report(override)
    await assert.rejects(client(async () => json(data)).generate(body), malformed)
    await assert.rejects(client(async () => json([data])).history(project), malformed)
    await assert.rejects(client(async () => json(data)).report(project, data.report_id), malformed)
    const message = { message_type: 'monitoring.report.ready', sender: 'monitoring_reporting', recipient: 'coordinator', correlation_id: data.request_id, project_id: project, report: data }
    await assert.rejects(client(async () => json(message)).coordinator(project, data.report_id), malformed)
  }
  const data = report({
    planning_context: { project_id: project, source_message: { data: { project_id: project } } },
    actual_context: { observation_receipt: { project_id: project, actual_progress: { project_id: project } } },
    retrieved_evidence: [{ project_id: project, document_id: 'document-1' }],
    risk_evidence_used: ['Legacy unstructured evidence', { project_id: project }],
    risk_knowledge_sources: [{ source: 'Source without a project claim' }],
  })
  assert.deepEqual(await client(async () => json(data)).generate(body), data)
})

test('report ID, generated request ID, and date mismatches are rejected', async () => {
  await assert.rejects(client(async () => json(report())).report(project, 'other-report'), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /mismatched/ })
  const body = { project_id: project, request_id: 'different-request', as_of: '2026-10-08' }
  await assert.rejects(client(async () => json(report())).generate(body), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /mismatched/ })
  await assert.rejects(client(async () => json(report({ as_of: '2026-10-07' }))).generate({ ...body, request_id: 'request-1' }), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /mismatched/ })
})

test('full coverage accepts the exact server date and method fields', async () => {
  const coverage = { planning_status: 'available', planning_freshness: 'unknown', actual_status: 'missing', risk_status: 'not_supplied', milestones_status: 'not_supplied', total_tasks: 0, fresh_actual_tasks: 0, missing_actual_tasks: 0, stale_actual_tasks: 0, planning_updated_on: null, planning_observed_on: '2026-10-08', actual_updated_on: null, actual_observed_on: null, risk_observed_on: null, expected_progress_method: 'calendar_estimate', actual_progress_method: 'unweighted_task_average' }
  assert.deepEqual((await client(async () => json(report({ coverage }))).report(project, 'report-1')).coverage, coverage)
  await assert.rejects(client(async () => json(report({ coverage: { ...coverage, risk_status: 'online' } }))).report(project, 'report-1'), /malformed/)
})

test('malformed nested report data cannot reach the renderer', async () => {
  for (const overrides of [{ warnings: [null] }, { risks: 'not-an-array' }, { milestones: [null] }, { metrics: { actual_progress: '0' } }, { tasks: [{ task_id: 't1', title: 'Task', status: 'stale', last_updated_on: 'invalid-date' }] }, { tasks: [{ task_id: 't1', title: 'Task', status: 'in_progress', delayed: 'false' }] }, { actual_context: [] }]) {
    await assert.rejects(client(async () => json(report(overrides))).report(project, 'report-1'), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /malformed/ })
  }
})

test('generation rejects identity and destination fields before sending', async () => {
  let calls = 0
  const api = client(async () => { calls += 1; return json(report()) })
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08' }
  await assert.rejects(api.generate({ ...body, principal: { subject: 'invented' } }), /malformed/)
  await assert.rejects(api.generate({ ...body, destination_url: 'https://other.example.test' }), /malformed/)
  assert.equal(calls, 0)
})

test('legacy full reports with null coverage and null metrics remain readable', async () => {
  const data = await client(async () => json(report({ coverage: null }))).report(project, 'report-1')
  assert.equal(data.coverage, null)
  assert.equal(data.metrics.actual_progress, null)
})

test('pre-aborted request does not reach fetch', async () => {
  const controller = new AbortController()
  controller.abort()
  let calls = 0
  await assert.rejects(client(async () => { calls += 1 }).session(controller.signal), error => error === controller.signal.reason && error.name === 'AbortError' && !(error instanceof MonitoringApiError))
  assert.equal(calls, 0)
})

test('abort is forwarded to the transport and is not converted to an offline error', async () => {
  const controller = new AbortController()
  const pending = client((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
  })).session(controller.signal)
  controller.abort()
  await assert.rejects(pending, error => error === controller.signal.reason && error.name === 'AbortError' && !(error instanceof MonitoringApiError))
})

test('an aborted transport that ignores its signal cannot supply a late response', async () => {
  const controller = new AbortController()
  let resolve
  const pending = client(() => new Promise(done => { resolve = done })).session(controller.signal)
  controller.abort()
  resolve(json({ subject: 'old', project_ids: [project], permissions: ['monitoring:read'] }))
  await assert.rejects(pending, error => error === controller.signal.reason && error.name === 'AbortError' && !(error instanceof MonitoringApiError))
})

test('transport AbortError is preserved without being labelled as network failure', async () => {
  const reason = new DOMException('Request cancelled', 'AbortError')
  await assert.rejects(client(async () => { throw reason }).session(), error => error === reason && !(error instanceof MonitoringApiError))
})

test('already classified client errors keep their metadata and identity', async () => {
  const reason = new MonitoringApiError('Safe HTTP message', 503)
  await assert.rejects(client(async () => { throw reason }).session(), error => error === reason && error.status === 503 && error.kind === 'http')
})

test('network failures are safe, readable offline errors', async () => {
  for (const ErrorType of [Error, TypeError]) {
    await assert.rejects(client(async () => { throw new ErrorType('secret host information') }).session(), error => error instanceof MonitoringApiError && error.status === 0 && error.kind === 'network' && /Cannot reach/.test(error.message) && !error.message.includes('secret host'))
  }
})

test('bounded requests time out without exposing transport details', async () => {
  const api = client((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('private transport deadline details')))), { timeoutMs: 5 })
  await assert.rejects(api.session(), {
    name: 'MonitoringApiError', status: 0, kind: 'timeout',
    message: 'Monitoring request timed out. Check the API and configured sources.',
  })
})

test('base URL cannot embed credentials, query secrets, or external plaintext HTTP', () => {
  for (const baseUrl of ['not a URL', 'https://user:password@example.test', 'https://example.test?token=secret', 'https://example.test#private-fragment', 'javascript:alert(1)', 'http://monitoring.example.test', 'http://localhost.example.test']) {
    assert.throws(() => createMonitoringApi({ baseUrl }), { name: 'MonitoringApiError', status: 0, kind: 'configuration', message: 'The configured Monitoring API URL is invalid.' })
  }
})

test('loopback HTTP remains available for local development', () => {
  for (const baseUrl of ['http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000']) assert.doesNotThrow(() => createMonitoringApi({ baseUrl }))
})

test('Coordinator getter encodes saved report path and validates metadata', async () => {
  const message = { message_type: 'monitoring.report.ready', sender: 'monitoring_reporting', recipient: 'coordinator', correlation_id: 'request-1', project_id: project, report: report() }
  const api = client(async url => {
    assert.ok(url.endsWith('/reports/report-1/coordinator-message'))
    return json(message)
  })
  assert.equal((await api.coordinator(project, 'report-1')).correlation_id, 'request-1')
  await assert.rejects(client(async () => json({ ...message, correlation_id: 'wrong' })).coordinator(project, 'report-1'), { name: 'MonitoringApiError', status: 0, kind: 'response', message: /metadata/ })
})

test('health accepts legacy and optional observation storage without changing four required capabilities', async () => {
  const capabilities = { auth_configured: true, planning_configured: true, risk_configured: false, storage_configured: false }
  for (const extra of [{}, { observations_storage_configured: true }, { observations_storage_configured: false }, { future_capability: { status: 'unknown' } }]) {
    const data = { status: 'ready', capabilities: { ...capabilities, ...extra }, live_sources_verified: false }
    assert.deepEqual(await client(async () => json(data)).health(), data)
  }
  for (const observations_storage_configured of [null, 0, 'false', {}, []]) {
    await assert.rejects(client(async () => json({ status: 'ready', capabilities: { ...capabilities, observations_storage_configured }, live_sources_verified: false })).health(), malformed)
  }
  for (const key of Object.keys(capabilities)) {
    const incomplete = { ...capabilities, observations_storage_configured: true }
    delete incomplete[key]
    await assert.rejects(client(async () => json({ status: 'ready', capabilities: incomplete, live_sources_verified: false })).health(), malformed)
  }
})

test('observation routes encode opaque IDs, fix history limit, and use private transport protections', async () => {
  const projectId = 'project /?&#雪'
  const observationId = 'observation /?&#雪'
  const data = receipt({ project_id: projectId, observation_id: observationId, actual_progress: actualProgress({ project_id: projectId }), persisted: true })
  const calls = []
  const api = client(async (url, init) => {
    calls.push({ url, init })
    assert.equal(init.headers.Authorization, 'Bearer scoped-test-token')
    assert.equal(init.headers.Accept, 'application/json')
    assert.equal(init.credentials, 'omit')
    assert.equal(init.cache, 'no-store')
    assert.equal(init.redirect, 'error')
    assert.equal(init.referrerPolicy, 'no-referrer')
    assert.ok(init.signal instanceof AbortSignal)
    assert.ok(!url.includes('scoped-test-token'))
    assert.ok(!init.body?.includes('scoped-test-token'))
    return json(url.endsWith('?limit=20') ? [data] : data)
  })
  const body = observationBody({ actual_progress: data.actual_progress, save_observation: true })
  assert.deepEqual(await api.submitObservation(projectId, body), data)
  assert.deepEqual(await api.observationHistory(projectId), [data])
  assert.deepEqual(await api.observation(projectId, observationId), data)
  const root = 'https://monitoring.example.test/api/monitoring/projects/project%20%2F%3F%26%23%E9%9B%AA/observations'
  assert.deepEqual(calls.map(call => [call.url, call.init.method]), [[root, 'POST'], [`${root}?limit=20`, 'GET'], [`${root}/observation%20%2F%3F%26%23%E9%9B%AA`, 'GET']])
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), body)
  for (const call of calls.slice(1)) {
    assert.equal(call.init.body, undefined)
    assert.equal(call.init.headers['Content-Type'], undefined)
  }
})

test('observation submission defaults saving to false without mutating caller data or adding dates', async () => {
  const body = observationBody()
  Object.freeze(body.actual_progress.tasks[0])
  Object.freeze(body.actual_progress.tasks)
  Object.freeze(body.actual_progress)
  Object.freeze(body)
  const data = await client(async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body), { ...body, save_observation: false })
    return json(receipt())
  }).submitObservation(project, body)
  assert.equal(data.persisted, false)
  assert.equal(Object.hasOwn(body, 'save_observation'), false)
  assert.equal(Object.hasOwn(data.actual_progress, 'updated_on'), false)
  assert.equal(Object.hasOwn(data.actual_progress.tasks[0], 'updated_on'), false)
})

test('observation submission preserves explicit save behavior and rejects mismatched persistence', async () => {
  for (const save_observation of [false, true]) {
    const body = observationBody({ save_observation })
    const data = await client(async (_url, init) => {
      assert.equal(JSON.parse(init.body).save_observation, save_observation)
      return json(receipt({ persisted: save_observation }))
    }).submitObservation(project, body)
    assert.equal(data.persisted, save_observation)
    await assert.rejects(client(async () => json(receipt({ persisted: !save_observation }))).submitObservation(project, body), malformed)
  }
  await assert.rejects(client(async () => json(receipt({ persisted: true }))).submitObservation(project, observationBody()), malformed)
})

test('observation submission rejects foreign projects and request, source, or date correlation mismatches', async () => {
  for (const overrides of [{ project_id: 'foreign-project' }, { actual_progress: actualProgress({ project_id: 'foreign-project' }) }, { request_id: 'wrong-request' }, { source: 'other-label' }, { observed_on: '2026-10-07' }]) {
    await assert.rejects(client(async () => json(receipt(overrides))).submitObservation(project, observationBody()), malformed)
  }
})

test('observation request validates identifiers, dates, booleans, and approved fields before fetch', async () => {
  let calls = 0
  const api = client(async () => { calls += 1; return json(receipt()) })
  for (const projectId of ['', '   ', null, 5, 'project\n']) await assert.rejects(api.submitObservation(projectId, observationBody()), malformed)
  for (const overrides of [
    { request_id: '' }, { request_id: 'bad request' }, { request_id: 'x'.repeat(101) },
    { source: '' }, { source: '   ' }, { source: 1 }, { source: 'https://user:secret@example.test' }, { source: 'Bearer private-secret' },
    { observed_on: '2026-02-30' }, { observed_on: '2026-10-08T00:00:00Z' }, { observed_on: '9999-01-01' },
    { save_observation: null }, { save_observation: 'false' }, { save_observation: 0 },
    { project_id: project }, { observation_id: 'caller-id' }, { submitted_by: 'caller' }, { subject: 'caller' },
    { principal: { subject: 'caller' } }, { destination_url: 'https://other.example.test' }, { token: 'private-secret' }, { credentials: {} },
    { actual_progress: actualProgress({ project_id: 'foreign-project' }) }, { actual_progress: actualProgress({ token: 'private-secret' }) },
    { actual_progress: null }, { actual_progress: [] },
  ]) await assert.rejects(api.submitObservation(project, observationBody(overrides)), malformed)
  assert.equal(calls, 0)
})

test('observation submission pairs dated Planning envelopes and checks their project and source date', async () => {
  const planning_message = { sender: 'planning_agent', receiver: 'project_monitor_report_agent', action: 'SCHEDULE_CREATED', request_id: 'planning-request', data: { project_id: project, updated_on: '2026-10-07' } }
  const body = observationBody({ planning_message, received_on: '2026-10-08' })
  await client(async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body), { ...body, save_observation: false })
    return json(receipt())
  }).submitObservation(project, body)
  let calls = 0
  const api = client(async () => { calls += 1 })
  for (const overrides of [
    { planning_message }, { received_on: '2026-10-08' },
    { planning_message: [], received_on: '2026-10-08' },
    { planning_message: { ...planning_message, data: { project_id: 'foreign-project' } }, received_on: '2026-10-08' },
    { planning_message, received_on: '2026-02-30' }, { planning_message, received_on: '9999-01-01' },
    { planning_message, received_on: '2026-10-06' },
  ]) await assert.rejects(api.submitObservation(project, observationBody(overrides)), malformed)
  assert.equal(calls, 0)
})

test('observation task validation accepts canonical provider statuses and explicit dated completion', () => {
  for (const status of ['NOT_STARTED', 'PLANNED', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED']) {
    const task = { task_id: 'task-1', status, progress_percentage: ['NOT_STARTED', 'PLANNED'].includes(status) ? 0 : status === 'COMPLETED' ? 100 : 35, ...(status === 'COMPLETED' ? { actual_start: '2026-10-01', actual_end: '2026-10-07', updated_on: '2026-10-08' } : {}) }
    const data = receipt({ actual_progress: actualProgress({ tasks: [task] }) })
    assert.equal(validateObservation(data, project), data)
    assert.deepEqual(data.actual_progress.tasks, [task])
  }
  const data = receipt({ actual_progress: actualProgress({ updated_on: '2026-10-07', tasks: [{ task_id: 'task-1', status: 'BLOCKED', progress_percentage: 0, actual_start: '2026-10-01', updated_on: '2026-10-07' }] }) })
  assert.equal(validateObservation(data, project), data)
})

test('malformed observation tasks and inconsistent dates fail in both caller and response validation', async () => {
  const task = actualProgress().tasks[0]
  const payloads = [
    actualProgress({ tasks: [] }), actualProgress({ tasks: 'not-a-list' }), actualProgress({ tasks: [null] }),
    actualProgress({ tasks: [task, { ...task }] }), actualProgress({ tasks: Array.from({ length: 1001 }, (_, index) => ({ ...task, task_id: `task-${index}` })) }),
    ...[
      { task_id: '' }, { task_id: 'bad id' }, { status: 'in_progress' }, { status: 'UNKNOWN' },
      { progress_percentage: undefined }, { progress_percentage: null }, { progress_percentage: '35' }, { progress_percentage: false },
      { progress_percentage: -1 }, { progress_percentage: 101 }, { progress_percentage: NaN }, { progress_percentage: Infinity },
      { status: 'NOT_STARTED', progress_percentage: 1 }, { status: 'PLANNED', actual_start: '2026-10-01', progress_percentage: 0 },
      { status: 'COMPLETED', progress_percentage: 99, actual_end: '2026-10-07' }, { status: 'COMPLETED', progress_percentage: 100 },
      { progress_percentage: 100 }, { actual_end: '2026-10-07' }, { actual_start: '2026-02-30' }, { updated_on: 'invalid' },
      { actual_start: '2026-10-09' }, { updated_on: '2026-10-09' },
      { status: 'COMPLETED', progress_percentage: 100, actual_end: '2026-10-09' },
      { status: 'COMPLETED', progress_percentage: 100, actual_start: '2026-10-07', actual_end: '2026-10-06' },
      { actual_start: '2026-10-07', updated_on: '2026-10-06' },
      { status: 'COMPLETED', progress_percentage: 100, actual_end: '2026-10-07', updated_on: '2026-10-06' },
      { submitted_by: 'caller' }, { destination_url: 'https://other.example.test' }, { token: 'private-secret' },
    ].map(overrides => actualProgress({ tasks: [{ ...task, ...overrides }] })),
    actualProgress({ updated_on: '2026-10-09' }), actualProgress({ updated_on: '2026-02-30' }),
    actualProgress({ updated_on: '2026-10-07' }),
    actualProgress({ updated_on: '2026-10-06', tasks: [{ ...task, updated_on: '2026-10-07' }] }),
  ]
  let calls = 0
  const api = client(async () => { calls += 1 })
  for (const actual_progress of payloads) {
    assert.throws(() => validateObservation(receipt({ actual_progress }), project), malformed)
    await assert.rejects(api.submitObservation(project, observationBody({ actual_progress })), malformed)
    await assert.rejects(client(async () => json(receipt({ actual_progress, persisted: true }))).observation(project, 'observation-1'), malformed)
  }
  assert.equal(calls, 0)
})

test('receipt validation requires typed metadata, valid UTC acceptance, and optional exact correlations', () => {
  const data = receipt()
  assert.equal(validateObservation(data, project, 'observation-1', 'observation-request-1'), data)
  assert.equal(validateObservation(receipt({ accepted_at: '2026-10-08T14:12:00.123456Z' }), project).persisted, false)
  assert.throws(() => validateObservation(data, 'foreign-project'), malformed)
  assert.throws(() => validateObservation(data, project, 'wrong-observation'), malformed)
  assert.throws(() => validateObservation(data, project, undefined, 'wrong-request'), malformed)
  for (const overrides of [
    { observation_id: '' }, { request_id: '' }, { source: null }, { submitted_by: '' }, { submitted_by: {} },
    { persisted: undefined }, { persisted: 'true' }, { persisted: 1 }, { limitations: null }, { limitations: [5] },
    { accepted_at: null }, { accepted_at: 1791468720 }, { accepted_at: '2026-10-08' }, { accepted_at: '2026-10-08T14:12:00' },
    { accepted_at: '2026-02-30T14:12:00Z' }, { accepted_at: '2026-10-08T24:12:00Z' }, { accepted_at: '2026-10-08T14:60:00Z' },
    { accepted_at: '2026-10-08T14:12:60Z' }, { accepted_at: '2026-10-08T14:12:00+05:30' },
    { observed_on: '2026-02-30' }, { observed_on: '9999-01-01' },
  ]) assert.throws(() => validateObservation(receipt(overrides), project), malformed)
})

test('project-local observation dates can follow the UTC acceptance date at midnight', async () => {
  const data = receipt({ accepted_at: '2026-10-07T19:00:00Z' })
  assert.equal(validateObservation(data, project), data)
  assert.deepEqual(await client(async () => json(data)).submitObservation(project, observationBody()), data)
  const stored = { ...data, persisted: true }
  assert.deepEqual(await client(async () => json([stored])).observationHistory(project), [stored])
  assert.deepEqual(await client(async () => json(stored)).observation(project, stored.observation_id), stored)
  assert.throws(() => validateObservation({ ...data, actual_progress: actualProgress({ updated_on: '2026-10-09' }) }, project), malformed)
})

test('source, submitter, and untyped extra receipt metadata do not imply verified origin or authorization', async () => {
  const data = receipt({ source: 'planning_agent', submitted_by: 'different-server-principal', status: { arbitrary: 'metadata' }, origin_verified: true })
  assert.equal(validateObservation(data, project), data)
  assert.deepEqual(await client(async () => json(data)).submitObservation(project, observationBody({ source: 'planning_agent' })), data)
  assert.equal(Object.hasOwn(data, 'permissions'), false)
})

test('stored observation history requires persisted full receipts, project match, and unique observation IDs', async () => {
  assert.deepEqual(await client(async () => json([])).observationHistory(project), [])
  const data = receipt({ persisted: true })
  assert.deepEqual(await client(async () => json([data])).observationHistory(project), [data])
  const repeatedRequest = [data, receipt({ observation_id: 'observation-2', persisted: true })]
  assert.deepEqual(await client(async () => json(repeatedRequest)).observationHistory(project), repeatedRequest)
  for (const response of [
    { observations: [data] }, [null], [receipt()], [receipt({ persisted: 'true' })],
    [receipt({ project_id: 'foreign-project', persisted: true })],
    [receipt({ actual_progress: actualProgress({ project_id: 'foreign-project' }), persisted: true })],
    [data, data],
    [data, receipt({ request_id: 'observation-request-2', persisted: true })],
    [receipt({ accepted_at: 'invalid', persisted: true })], [receipt({ actual_progress: actualProgress({ tasks: [null] }), persisted: true })],
        Array.from({ length: 21 }, (_, index) => receipt({ observation_id: `observation-${index}`, request_id: `observation-request-${index}`, persisted: true })),
  ]) await assert.rejects(client(async () => json(response)).observationHistory(project), malformed)
})

test('stored observation getter requires persistence and exact project and observation ID', async () => {
  for (const overrides of [{}, { persisted: 'true' }, { persisted: true, project_id: 'foreign-project' }, { persisted: true, observation_id: 'wrong-id' }, { persisted: true, request_id: '' }]) {
    await assert.rejects(client(async () => json(receipt(overrides))).observation(project, 'observation-1'), malformed)
  }
  let calls = 0
  const api = client(async () => { calls += 1 })
  for (const invalidId of ['', '   ', null, 1]) {
    await assert.rejects(api.observationHistory(invalidId), malformed)
    await assert.rejects(api.observation(invalidId, 'observation-1'), malformed)
    await assert.rejects(api.observation(project, invalidId), malformed)
  }
  assert.equal(calls, 0)
})

test('generation forwards a stored observation ID without extra identity or inline data', async () => {
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08', observation_id: 'observation-1', include_risks: false, save_report: false }
  const data = await client(async (url, init) => {
    assert.equal(url, 'https://monitoring.example.test/api/monitoring/reports')
    assert.equal(init.method, 'POST')
    assert.equal(init.headers.Authorization, 'Bearer scoped-test-token')
    assert.deepEqual(JSON.parse(init.body), body)
    return json(report())
  }).generate(body)
  assert.equal(data.report_id, 'report-1')
})

test('stored generation rejects inline fields, invalid stored IDs, and extra identity before sending', async () => {
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08', observation_id: 'observation-1' }
  let calls = 0
  const api = client(async () => { calls += 1 })
  for (const overrides of [
    { actual_progress: actualProgress() }, { observed_on: '2026-10-08' }, { actual_progress: null }, { observed_on: null },
    { actual_progress: actualProgress(), observed_on: '2026-10-08' },
    { observation_id: '' }, { observation_id: '   ' }, { observation_id: null }, { observation_id: 1 },
    { principal: { subject: 'caller' } }, { submitted_by: 'caller' }, { token: 'private-secret' }, { source: 'manual' },
    { destination_url: 'https://other.example.test' },
  ]) await assert.rejects(api.generate({ ...body, ...overrides }), malformed)
  assert.equal(calls, 0)
})

test('legacy inline generation remains supported with project, observation, and report date checks', async () => {
  const body = { project_id: project, request_id: 'request-1', as_of: '2026-10-08', actual_progress: actualProgress(), observed_on: '2026-10-08' }
  await client(async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body), body)
    return json(report())
  }).generate(body)
  let calls = 0
  const api = client(async () => { calls += 1 })
  for (const overrides of [{ observed_on: null }, { actual_progress: null }, { observed_on: '2026-10-09' }, { actual_progress: actualProgress({ project_id: 'foreign-project' }) }, { as_of: '2026-02-30' }, { as_of: '9999-01-01' }, { include_risks: 'false' }, { save_report: 1 }]) {
    await assert.rejects(api.generate({ ...body, ...overrides }), malformed)
  }
  assert.equal(calls, 0)
})

const observationOperations = [
  ['submit', (api, signal) => api.submitObservation(project, observationBody(), signal)],
  ['history', (api, signal) => api.observationHistory(project, signal)],
  ['get', (api, signal) => api.observation(project, 'observation-1', signal)],
]
for (const [name, run] of observationOperations) {
  test(`observation ${name} requires scoped auth and never reads or persists browser token storage`, async () => {
    const keys = ['localStorage', 'sessionStorage']
    const descriptors = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
    for (const key of keys) Object.defineProperty(globalThis, key, { configurable: true, get() { throw new Error('Token storage must not be accessed') } })
    try {
      const data = receipt({ persisted: name !== 'submit' })
      await run(client(async (_url, init) => {
        assert.equal(init.headers.Authorization, 'Bearer scoped-test-token')
        return json(name === 'history' ? [data] : data)
      }))
      let calls = 0
      for (const token of ['', '   ', 'bad\r\ntoken']) {
        await assert.rejects(run(client(async () => { calls += 1 }, { token })), { name: 'MonitoringApiError', status: 401, kind: 'http' })
      }
      assert.equal(calls, 0)
    } finally {
      keys.forEach((key, index) => {
        if (descriptors[index]) Object.defineProperty(globalThis, key, descriptors[index])
        else delete globalThis[key]
      })
    }
  })

  test(`observation ${name} propagates abort and safe bounded timeout errors`, async () => {
    const controller = new AbortController()
    let calls = 0
    controller.abort()
    await assert.rejects(run(client(async () => { calls += 1 }), controller.signal), error => error === controller.signal.reason)
    assert.equal(calls, 0)
    const active = new AbortController()
    const pending = run(client((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }))), active.signal)
    active.abort()
    await assert.rejects(pending, error => error === active.signal.reason)
    await assert.rejects(run(client((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('private deadline detail')), { once: true })), { timeoutMs: 5 })), {
      name: 'MonitoringApiError', status: 0, kind: 'timeout', message: 'Monitoring request timed out. Check the API and configured sources.',
    })
  })

  test(`observation ${name} keeps HTTP and transport errors classified without echoing secrets`, async () => {
    for (const status of [401, 403, 404, 409, 422, 502, 503, 504]) {
      const response = json({ detail: 'private-server-secret', input: 'private-token' }, status)
      response.json = async () => { throw new Error('Error response bodies must not be read') }
      await assert.rejects(run(client(async () => response)), error => error instanceof MonitoringApiError && error.status === status && error.kind === 'http' && !/private-server-secret|private-token/.test(error.message))
    }
    await assert.rejects(run(client(async () => { throw new Error('private-host-secret') })), { name: 'MonitoringApiError', status: 0, kind: 'network', message: 'Cannot reach the Monitoring API. Check its configuration, availability, and CORS policy.' })
    await assert.rejects(run(client(async () => new Response('<html>private-server-secret</html>'))), { name: 'MonitoringApiError', status: 0, kind: 'response', message: 'Monitoring returned invalid JSON. The response was not used.' })
  })
}
