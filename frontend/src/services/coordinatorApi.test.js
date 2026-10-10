import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { CoordinatorApiError, coordinatorErrorMessage, createCoordinatorApi } from './coordinatorApi.js'

// Every transport is synthetic; an accidental default fetch must fail offline.
globalThis.fetch = async () => { throw new Error('Live network is forbidden in Coordinator tests') }

const clock = 1800000000000
const expiry = clock / 1000 + 300
const userId = '11111111-1111-4111-8111-111111111111'
const encode = value => btoa(Array.from(new TextEncoder().encode(JSON.stringify(value)), byte => String.fromCharCode(byte)).join('')).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
const jwt = overrides => `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: 'alice', account_id: userId, role: 'user', iat: clock / 1000 - 10, exp: expiry, iss: 'apm', aud: 'apm-accounts', token_use: 'account_access', ...overrides })}.c2lnbmF0dXJl`
const user = overrides => ({ username: 'alice', user_id: userId, role: 'user', expires_at: expiry, token_type: 'bearer', access_token: jwt(), ...overrides })
const admin = () => ({ username: 'admin', role: 'admin', expires_at: expiry, token_type: 'bearer', access_token: jwt({ sub: 'admin', role: 'admin', account_id: undefined, aud: 'apm-admin', token_use: 'admin_access' }) })
const managedAdmin = () => user({ role: 'admin', access_token: jwt({ role: 'admin' }) })
const text = 'Project: Inventory. Build stock tracking. Start date: 2026-10-12. Deadline: 2026-12-12. We have 3 developers.'
const result = () => ({ method: 'rule_based', draft: { project_name: 'Inventory', objective: text, message: text, start_date: '2026-10-12', deadline: '2026-12-12', resources: [{ resource_id: 'R1', name: 'Developers', capacity: 3 }] }, missing_fields: [], notes: ['Review the details before Planning.'] })
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const api = (fetchImpl, options = {}) => createCoordinatorApi({ baseUrl: 'https://coordinator.example.test', fetchImpl, now: () => clock, ...options })
const errorOf = kind => ({ name: 'CoordinatorApiError', kind })
const abortOf = { name: 'AbortError', message: 'Coordinator request cancelled.' }
const tick = () => new Promise(resolve => setTimeout(resolve, 0))

for (const [name, session] of [['user', user()], ['bootstrap admin', admin()], ['managed admin', managedAdmin()]]) {
  test(`${name} interpretation sends only its bearer and text with private transport`, async () => {
    let call
    const response = await api(async (url, init) => { call = { url, init }; return json(result()) }).interpretProjectInput(text, { session, headers: { 'X-API-Key': 'injected' }, apiKey: 'injected' })
    assert.deepEqual(response, result())
    assert.equal(call.url, 'https://coordinator.example.test/api/coordinator/interpret')
    assert.equal(call.init.method, 'POST')
    assert.deepEqual(call.init.headers, { Accept: 'application/json', Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' })
    assert.deepEqual(JSON.parse(call.init.body), { text })
    assert.equal(call.init.credentials, 'omit')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(call.init.redirect, 'error')
    assert.equal(call.init.referrerPolicy, 'no-referrer')
    assert.ok(call.init.signal instanceof AbortSignal)
    assert.equal(call.url.includes(session.access_token), false)
  })
}

test('missing, expired, inconsistent, and service sessions fail before transport', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json(result()) })
  for (const session of [undefined, null, {}, { role: 'service', access_token: 'service-key' }, user({ expires_at: clock / 1000 }), user({ role: 'admin' }), user({ user_id: 'another-id' }), user({ access_token: jwt({ role: 'admin' }) }), user({ access_token: jwt({ aud: 'wrong-audience' }) }), user({ token_type: 'basic' })]) {
    let rejected
    await assert.rejects(client.interpretProjectInput(text, { session, onUnauthorized: value => { rejected = value } }), error => error.status === 401)
    assert.strictEqual(rejected, session)
  }
  assert.equal(calls, 0)
})

test('invalid descriptions fail safely without transport or unauthorized callbacks', async () => {
  let calls = 0
  let rejected = 0
  const client = api(async () => { calls += 1; return json(result()) })
  for (const input of [null, {}, '', 'short', '          ', 'x'.repeat(5001)]) {
    await assert.rejects(client.interpretProjectInput(input, { session: user(), onUnauthorized: () => { rejected += 1 } }), errorOf('input'))
  }
  assert.equal(calls, 0)
  assert.equal(rejected, 0)
})

test('HTTP errors never consume server error bodies and only 401 rejects the exact session', async () => {
  for (const status of [400, 401, 403, 404, 422, 429, 500, 503]) {
    let reads = 0
    const rejected = []
    const original = user()
    await assert.rejects(api(async () => ({ ok: false, status, json() { reads += 1; throw new Error('private credentials and project description') } })).interpretProjectInput(text, { session: original, onUnauthorized: session => { rejected.push(session) } }), error => {
      assert.equal(error.kind, 'http')
      assert.equal(error.status, status)
      assert.equal(coordinatorErrorMessage(error).includes('private credentials'), false)
      return true
    })
    assert.equal(reads, 0)
    assert.equal(rejected.length, status === 401 ? 1 : 0)
    if (status === 401) assert.strictEqual(rejected[0], original)
  }
})

test('a delayed 401 refers to the originating session, not a newer login or validation clone', async () => {
  let resolve
  const original = user()
  const newer = user({ access_token: jwt({ iat: clock / 1000 - 5 }) })
  let current = original
  const rejected = []
  const pending = api(() => new Promise(done => { resolve = done })).interpretProjectInput(text, { session: original, onUnauthorized: session => {
    rejected.push(session)
    if (current === session) current = null
  } })
  current = newer
  resolve(json({ detail: 'must not be consumed' }, 401))
  await assert.rejects(pending, error => error.status === 401)
  assert.strictEqual(rejected[0], original)
  assert.strictEqual(current, newer)
})

test('sync and async cleanup failures cannot replace safe authentication errors', async () => {
  for (const onUnauthorized of [() => { throw new Error('private cleanup error') }, async () => { throw new Error('private cleanup error') }]) {
    await assert.rejects(api(async () => json({}, 401)).interpretProjectInput(text, { session: user(), onUnauthorized }), error => error.status === 401 && !error.message.includes('private'))
    await tick()
  }
})

test('redirects, foreign URLs, wrong status, content type, and malformed JSON fail closed', async () => {
  for (const response of [
    { ok: true, status: 200, redirected: true },
    { ok: false, status: 401, redirected: true },
    { ok: true, status: 200, type: 'opaqueredirect' },
    { ok: true, status: 200, url: 'https://foreign.example.test' },
    json(result(), 201),
    new Response('not JSON', { headers: { 'Content-Type': 'application/json' } }),
    new Response(JSON.stringify(result()), { headers: { 'Content-Type': 'text/html' } }),
  ]) {
    let rejected = 0
    await assert.rejects(api(async () => response).interpretProjectInput(text, { session: user(), onUnauthorized: () => { rejected += 1 } }), errorOf('response'))
    assert.equal(rejected, 0)
  }
})

test('invalid draft shapes cannot overwrite the editable form', async () => {
  for (const mutate of [
    data => { data.method = 'unknown' }, data => { data.draft = null },
    data => { data.draft.objective = {} }, data => { data.draft.message = 123 },
    data => { data.draft.project_name = 'x'.repeat(201) }, data => { data.draft.deadline = 'tomorrow' },
    data => { data.missing_fields = ['access_token'] }, data => { data.notes = [{}] },
    data => { data.draft.resources = {} }, data => { data.draft.resources = [null] },
    data => { data.draft.resources[0].capacity = 0 }, data => { data.draft.resources[0].capacity = 1.5 },
    data => { data.draft.resources.push({ ...data.draft.resources[0] }) },
  ]) {
    const data = result()
    mutate(data)
    await assert.rejects(api(async () => json(data)).interpretProjectInput(text, { session: user() }), errorOf('response'))
  }
})

test('partial extraction is preserved and injected identities, tasks and transport fields are discarded', async () => {
  const data = result()
  delete data.draft.project_name
  delete data.draft.start_date
  delete data.draft.deadline
  data.missing_fields = ['project_name', 'start_date', 'deadline']
  const expected = structuredClone(data)
  Object.assign(data.draft, { role: 'admin', owner_user_id: 'another-user', access_token: 'secret', tasks: [{ task_id: 'injected' }] })
  data.draft.resources[0].access_token = 'secret'
  data.access_token = 'secret'
  assert.deepEqual(await api(async () => json(data)).interpretProjectInput(text, { session: user() }), expected)
})

test('unsafe URL and timeout configuration cannot construct a transport', () => {
  for (const baseUrl of ['http://public.example.test', 'https://user:secret@example.test', 'https://example.test/api', 'https://example.test?token=secret', 'https://example.test#token', '']) {
    assert.throws(() => api(async () => json(result()), { baseUrl }), errorOf('configuration'))
  }
  for (const timeoutMs of [0, -1, 1.5, Infinity, 2147483648]) assert.throws(() => api(async () => json(result()), { timeoutMs }), errorOf('configuration'))
})

test('same-origin proxy is the default and local development HTTP remains allowed', async () => {
  for (const options of [{ origin: 'http://localhost:5173' }, { baseUrl: 'http://127.0.0.1:8000' }]) {
    let url
    await createCoordinatorApi({ ...options, now: () => clock, fetchImpl: async value => { url = value; return json(result()) } }).interpretProjectInput(text, { session: user() })
    assert.equal(url, `${options.origin || options.baseUrl}/api/coordinator/interpret`)
  }
})

test('pre-aborted requests never validate, fetch or reject the session', async () => {
  const controller = new AbortController()
  controller.abort('private cancellation reason')
  let calls = 0
  let rejected = 0
  await assert.rejects(api(async () => { calls += 1; return json(result()) }).interpretProjectInput(text, { signal: controller.signal, onUnauthorized: () => { rejected += 1 } }), abortOf)
  assert.equal(calls, 0)
  assert.equal(rejected, 0)
})

for (const status of [200, 401]) {
  test(`late ${status} after cancellation cannot deliver a draft or reject a session`, async () => {
    let resolve
    let transportSignal
    let rejected = 0
    const controller = new AbortController()
    const pending = api((_url, init) => { transportSignal = init.signal; return new Promise(done => { resolve = done }) }).interpretProjectInput(text, { session: user(), signal: controller.signal, onUnauthorized: () => { rejected += 1 } })
    controller.abort('private reason')
    await assert.rejects(pending, abortOf)
    assert.equal(transportSignal.aborted, true)
    resolve(json(result(), status))
    await tick()
    assert.equal(rejected, 0)
  })
}

test('cancellation during body reading cannot apply the draft', async () => {
  let resolveBody
  const controller = new AbortController()
  const pending = api(async () => ({ ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => new Promise(done => { resolveBody = done }) })).interpretProjectInput(text, { session: user(), signal: controller.signal })
  await tick()
  controller.abort()
  await assert.rejects(pending, abortOf)
  resolveBody(result())
  await tick()
})

test('timeout bounds transports and body readers that ignore abort', async () => {
  for (const fetchImpl of [() => new Promise(() => {}), async () => ({ ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => new Promise(() => {}) })]) {
    await assert.rejects(api(fetchImpl, { timeoutMs: 5 }).interpretProjectInput(text, { session: user() }), errorOf('timeout'))
  }
})

test('late 401 after timeout does not invalidate a session', async () => {
  let resolve
  let rejected = 0
  const pending = api(() => new Promise(done => { resolve = done }), { timeoutMs: 5 }).interpretProjectInput(text, { session: user(), onUnauthorized: () => { rejected += 1 } })
  await assert.rejects(pending, errorOf('timeout'))
  resolve(json({}, 401))
  await tick()
  assert.equal(rejected, 0)
})

test('session expiry is rechecked before accepting extraction and rejects the original object', async () => {
  let now = clock
  let rejected
  const original = user()
  await assert.rejects(api(async () => { now = expiry * 1000; return json(result()) }, { now: () => now }).interpretProjectInput(text, { session: original, onUnauthorized: session => { rejected = session } }), error => error.status === 401)
  assert.strictEqual(rejected, original)
})

test('network and unknown errors never expose raw details', async () => {
  await assert.rejects(api(async () => { throw new Error('private credentials') }).interpretProjectInput(text, { session: user() }), errorOf('network'))
  await assert.rejects(api(async () => json(result()), { now: () => { throw new Error('private clock details') } }).interpretProjectInput(text, { session: user() }), errorOf('response'))
  assert.equal(coordinatorErrorMessage(new Error('private credentials')), 'The server returned invalid project details. They were not used.')
  assert.match(coordinatorErrorMessage(new CoordinatorApiError('http', 403)), /permission/)
})

test('page wiring keeps session isolation, cancellation and draft-to-Planning autofill', async () => {
  const page = await readFile(new URL('../pages/Coordinator.jsx', import.meta.url), 'utf8')
  const service = await readFile(new URL('./coordinatorApi.js', import.meta.url), 'utf8')
  assert.match(page, /function Coordinator\(\{ session, onUnauthorized \}\)/)
  assert.match(page, /<CoordinatorWorkspace key=\{key\} session=\{session\} onUnauthorized=\{onUnauthorized\}/)
  assert.match(page, /interpretProjectInput\(text, \{ session, signal: controller.signal, onUnauthorized \}\)/)
  assert.match(page, /lifecycle\.controller\?\.abort\(\)/)
  assert.match(page, /if \(!isCurrent\(\)\) return/)
  assert.match(page, /if \(!isCurrent\(\) \|\| exc\?\.name === "AbortError"\) return/)
  assert.match(page, /setError\(coordinatorErrorMessage\(exc\)\)/)
  assert.match(page, /onClick=\{cancelInterpretation\}/)
  assert.match(page, /setForm\(\{ \.\.\.initial, \.\.\.data\.draft \}\)/)
  assert.match(page, /setResources\(data\.draft\.resources \|\| \[\]\); setTasks\(\[\]\)/)
  assert.match(page, /navigate\("\/planning", \{ state: \{ coordinatorDraft:/)
  assert.match(page, /project_name: name, objective: combined/)
  assert.match(page, /start_date: form\.start_date, deadline: form\.deadline/)
  assert.match(page, /resources, tasks, risks: \[\]/)
  assert.doesNotMatch(`${page}\n${service}`, /VITE_PLANNING_API_KEY|X-API-Key|submitCoordinatorRequest/)
})
