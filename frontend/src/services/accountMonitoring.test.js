import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { monitoringAccountKey, verifyAccountMonitoringSession } from './accountMonitoring.js'
import { createMonitoringApi, MonitoringApiError } from './monitoringApi.js'

const clock = 1800000000000
const now = () => clock
const userId = '1b5e7946-0f72-4a41-9c5e-4184c817790b'
const otherId = 'aa2db83e-3b6d-4998-b3c6-d605b4d9b30e'
const expiry = clock / 1000 + 300
const encode = value => btoa(Array.from(new TextEncoder().encode(JSON.stringify(value)), byte => String.fromCharCode(byte)).join('')).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
const jwt = claims => `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(claims)}.c2lnbmF0dXJl`
const account = (role = 'user') => Object.freeze({
  username: 'alice', user_id: userId, role, token_type: 'bearer', expires_at: expiry,
  access_token: jwt({ sub: 'alice', account_id: userId, role, token_use: 'account_access', exp: expiry, iat: clock / 1000 - 10, aud: 'apm-accounts', iss: 'apm' }),
})
const bootstrap = () => Object.freeze({
  username: '管理员😀', role: 'admin', token_type: 'bearer', expires_at: expiry,
  access_token: jwt({ sub: '管理员😀', role: 'admin', token_use: 'legacy_admin', exp: expiry }),
})
const metadata = (subject = userId, project_ids = ['owned-project']) => ({ subject, project_ids, permissions: ['monitoring:read', 'monitoring:generate'] })
const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
const api = (session, fetchImpl) => createMonitoringApi({ baseUrl: 'https://monitoring.example.test', token: session.access_token, fetchImpl })
const responseError = { name: 'MonitoringApiError', status: 0, kind: 'response' }

for (const role of ['user', 'admin']) {
  test(`${role} account verifies shared bearer with UUID subject, not username`, async () => {
    const session = account(role)
    const ids = role === 'admin' ? ['owned-project', 'another-users-project'] : ['owned-project']
    const result = await verifyAccountMonitoringSession(session, { now, api: api(session, async (url, init) => {
      assert.equal(url, 'https://monitoring.example.test/api/monitoring/session')
      assert.equal(init.headers.Authorization, `Bearer ${session.access_token}`)
      assert.equal(init.credentials, 'omit')
      assert.equal(init.cache, 'no-store')
      assert.equal(init.body, undefined)
      assert.ok(!url.includes(session.access_token))
      return json(metadata(userId, ids))
    }) })
    assert.notEqual(result.subject, session.username)
    assert.deepEqual(result.project_ids, ids)
    assert.ok(Object.isFrozen(result) && Object.isFrozen(result.project_ids))
  })
}

test('bootstrap administrator accepts stable ASCII adapter identity after bearer verification', async () => {
  const session = bootstrap()
  let calls = 0
  const result = await verifyAccountMonitoringSession(session, { now, api: api(session, async (_url, init) => {
    calls += 1
    assert.equal(init.headers.Authorization, `Bearer ${session.access_token}`)
    return json(metadata('bootstrap-admin', ['all-project-1', 'all-project-2']))
  }) })
  assert.equal(calls, 1)
  assert.equal(result.subject, 'bootstrap-admin')
  assert.notEqual(result.subject, session.username)
})

test('incompatible project counts are disclosed without granting their IDs', async () => {
  const session = account()
  const result = await verifyAccountMonitoringSession(session, { now, api: { session: async () => ({ ...metadata(), unsupported_project_count: 2 }) } })
  assert.equal(result.unsupported_project_count, 2)
  assert.deepEqual(result.project_ids, ['owned-project'])
  for (const unsupported_project_count of [-1, 1.5, '2', null, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(verifyAccountMonitoringSession(session, { now, api: { session: async () => ({ ...metadata(), unsupported_project_count }) } }), responseError)
  }
  const page = await readFile(new URL('../pages/Monitoring.jsx', import.meta.url), 'utf8')
  assert.match(page, /connection\?\.unsupported_project_count > 0/)
  assert.match(page, /These projects are unchanged and remain available in Schedule/)
})

test('no local account/session claim creates a Monitoring grant', async () => {
  const session = account()
  for (const response of [metadata(otherId), metadata(session.username), null, {}, { ...metadata(), project_ids: ['owned-project', 'owned-project'] }, { ...metadata(), permissions: ['monitoring:read', 'monitoring:read'] }]) {
    await assert.rejects(verifyAccountMonitoringSession(session, { now, api: { session: async () => response } }), responseError)
  }
})

test('invalid or expired account never sends a Monitoring session request', async () => {
  let calls = 0
  for (const session of [null, {}, { ...account(), access_token: 'opaque-browser-token' }, { ...account(), user_id: otherId }, { ...account(), username: 'another-user' }, { ...account(), role: 'operator' }, { ...account(), expires_at: clock / 1000 }]) {
    await assert.rejects(verifyAccountMonitoringSession(session, { now, api: { session: async () => { calls += 1; return metadata() } } }), { name: 'MonitoringApiError', status: 401 })
  }
  assert.equal(calls, 0)
})

test('refresh verifies again and does not keep an earlier owned/all-project list on failure or revocation', async () => {
  const session = account()
  let calls = 0
  const client = api(session, async () => {
    calls += 1
    if (calls === 1) return json(metadata())
    if (calls === 2) return new Response('private-server-detail', { status: 503 })
    return json(metadata(userId, []))
  })
  assert.deepEqual((await verifyAccountMonitoringSession(session, { api: client, now })).project_ids, ['owned-project'])
  await assert.rejects(verifyAccountMonitoringSession(session, { api: client, now }), { name: 'MonitoringApiError', status: 503 })
  assert.deepEqual((await verifyAccountMonitoringSession(session, { api: client, now })).project_ids, [])
  assert.equal(calls, 3)
})

test('every HTTP access failure preserves its status and never exposes server details', async () => {
  const session = account()
  for (const status of [401, 403, 404, 503]) {
    await assert.rejects(verifyAccountMonitoringSession(session, { now, api: api(session, async () => new Response('private-server-detail', { status })) }), error => {
      assert.ok(error instanceof MonitoringApiError)
      assert.equal(error.status, status)
      assert.doesNotMatch(error.message, /private-server-detail/)
      return true
    })
  }
})

test('a cancelled check cannot grant access or become a late authentication failure', async () => {
  for (const outcome of [metadata(), null, new MonitoringApiError('Old session', 401)]) {
    const session = account()
    const controller = new AbortController()
    let settle
    const pending = verifyAccountMonitoringSession(session, { now, signal: controller.signal, api: api(session, () => new Promise((resolve, reject) => { settle = () => outcome instanceof Error ? reject(outcome) : resolve(json(outcome)) })) })
    controller.abort()
    await assert.rejects(pending, error => error === controller.signal.reason)
    settle()
  }
})

test('pre-cancelled and expired-in-flight verification cannot return a grant', async () => {
  const controller = new AbortController()
  controller.abort()
  let calls = 0
  await assert.rejects(verifyAccountMonitoringSession(account(), { now, signal: controller.signal, api: { session: async () => { calls += 1 } } }), { name: 'AbortError' })
  assert.equal(calls, 0)
  let current = clock
  await assert.rejects(verifyAccountMonitoringSession(account(), { now: () => current, api: { session: async () => { current = expiry * 1000; return metadata() } } }), { name: 'MonitoringApiError', status: 401 })
})

test('session replacement keys distinguish exact objects without containing bearer secrets', () => {
  const session = account()
  const key = monitoringAccountKey(session)
  assert.equal(monitoringAccountKey(session), key)
  assert.notEqual(monitoringAccountKey({ ...session }), key)
  assert.notEqual(monitoringAccountKey(account('admin')), key)
  assert.equal(monitoringAccountKey(null), 'signed-out')
  assert.ok(!String(key).includes(session.access_token))
})

// Source contracts supplement transport tests without a browser, bundler, env
// loading, or added test dependencies. Parent validation exercises real rendering.
test('both Monitoring routes use the account-verified workspace gate', async () => {
  const app = await readFile(new URL('../App.jsx', import.meta.url), 'utf8')
  for (const path of ['/monitoring', '/monitoring/:projectId']) {
    const line = app.split('\n').find(line => line.includes(`path="${path}"`))
    assert.match(line, /ProjectWorkspacePage session=\{accountSession \?\? session\} onLogout=\{clearProjectSession\} Page=\{Monitoring\}/)
  }
  assert.match(app, /return <Page session=\{session\} onUnauthorized=\{onLogout\}/)
  assert.match(app, /current === expectedSession \? null : current/)
})

test('Monitoring UI has signed-in chooser and no browser token/setup form', async () => {
  const page = await readFile(new URL('../pages/Monitoring.jsx', import.meta.url), 'utf8')
  assert.match(page, /Monitoring\(\{ session, onUnauthorized \}\)/)
  assert.match(page, /verifyAccountMonitoringSession\(session, \{ signal \}\)/)
  assert.match(page, /Administrator · all projects/)
  assert.match(page, /User · owned projects/)
  assert.match(page, /'All projects' : 'Owned projects'/)
  assert.match(page, /Refresh projects/)
  assert.match(page, /Retry Monitoring verification/)
  assert.doesNotMatch(page, /TokenHelp|MONITORING_AUTH_IDENTITIES|setToken|type="password"|scoped Monitoring token|Disconnect & forget/)
  assert.doesNotMatch(page, /localStorage|sessionStorage/)
  for (const feature of ['buildMonitoringRequest', 'ObservationIntake', 'ReportComparison', 'SavedTrends', 'reportCsv', 'feedbackExport', 'coordinatorExport']) assert.ok(page.includes(feature))
})

test('UI contracts gate project requests and discard state on routes, refresh, denial, logout and replacement', async () => {
  const page = await readFile(new URL('../pages/Monitoring.jsx', import.meta.url), 'utf8')
  assert.match(page, /if \(!session\) return <Notice>Sign in/)
  assert.match(page, /key=\{`\$\{monitoringAccountKey\(session\)\}-\$\{location.key\}-\$\{routeProjectId \|\| ''\}-\$\{attempt\}`\}/)
  assert.match(page, /onRefresh=\{refreshAccess\}/)
  assert.match(page, /setAttempt\(value => value \+ 1\)/)
  assert.match(page, /onRefresh\('import'\)/)
  assert.match(page, /initialSource=\{initialSource\}/)
  assert.match(page, /props.initialSource === 'import' \|\| !canRead \? 'import' : 'live'/)
  assert.match(page, /const connection = accessFailure \? null : connect.value/)
  assert.match(page, /routeProjectId && projectIds.includes\(routeProjectId\) \? routeProjectId : ''/)
  assert.match(page, /connection && selectedProject \? <ProjectWorkspace/)
  assert.match(page, /token=\{session.access_token\}/)
  assert.match(page, /if \(failure.status === 401\) onUnauthorized\?\.\(session\)/)
  assert.doesNotMatch(page, /onUnauthorized\?\.\(\)/)
  assert.match(page, /\[401, 403, 404\].includes\(failure.status\)/)
  assert.match(page, /sequence.current !== current \|\| next.signal.aborted/)
  assert.match(page, /setState\(\{ loading: false, value: null, error: message, failure \}\)/)
  assert.doesNotMatch(page, /retainValue|previous.value|setConnection/)
  assert.match(page, /sequence.current \+= 1\s+controller.current\?\.abort\(\)/)
})
