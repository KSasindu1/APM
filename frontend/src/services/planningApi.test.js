import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { PlanningApiError, buildPlanningRequest, createPlanningApi, planningErrorMessage } from './planningApi.js'

const clock = 1800000000000
const expiry = clock / 1000 + 300
const userId = '11111111-1111-4111-8111-111111111111'
const encode = value => btoa(Array.from(new TextEncoder().encode(JSON.stringify(value)), byte => String.fromCharCode(byte)).join('')).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
const jwt = overrides => `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: 'alice', account_id: userId, role: 'user', iat: clock / 1000 - 10, exp: expiry, iss: 'apm', aud: 'apm-accounts', token_use: 'account_access', ...overrides })}.c2lnbmF0dXJl`
const user = overrides => ({ username: 'alice', user_id: userId, role: 'user', expires_at: expiry, token_type: 'bearer', access_token: jwt(), ...overrides })
const admin = () => ({ username: 'admin', role: 'admin', expires_at: expiry, token_type: 'bearer', access_token: jwt({ sub: 'admin', role: 'admin', account_id: undefined, aud: 'apm-admin', token_use: 'admin_access' }) })
const project = 'project /?&#雪'
const schedule = overrides => ({ project_id: project, project_name: 'Demo project', schedule: [], ...overrides })
const form = { project_id: project, project_name: 'Demo project', objective: 'A private project objective', start_date: '2026-10-01', deadline: '2026-11-01', use_llm: false }
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const api = (fetchImpl, options = {}) => createPlanningApi({ baseUrl: 'https://planning.example.test', fetchImpl, now: () => clock, ...options })
const errorOf = kind => ({ name: 'PlanningApiError', kind })
const abortOf = { name: 'AbortError', message: 'Planning request cancelled.' }
const ownership = overrides => ({ project_id: project, owner_user_id: userId, owner_username: 'alice', ...overrides })

const requests = {
  list: client => client.getAllSchedules({ session: user() }),
  get: client => client.getSchedule(project, { session: user() }),
  create: client => client.createSchedule(form, { session: user() }),
  update: client => client.updateSchedule(project, form, { session: user() }),
  delete: client => client.deleteSchedule(project, { session: user() }),
  assign: client => client.assignProjectOwner(project, 'alice', { session: admin() }),
}
const responseFor = method => method === 'list' ? [schedule()] : method === 'delete' ? { project_id: project, deleted: true } : method === 'assign' ? ownership() : schedule()

test('planning payload excludes client-supplied ownership and role', () => {
  const result = buildPlanningRequest({ ...form, owner_user_id: 'another-user', user_id: 'another-user', role: 'admin', headers: { 'X-API-Key': 'injected-key' } })
  assert.deepEqual(result, { ...form, tasks: [], resources: [], risks: [] })
  assert.equal(Object.hasOwn(result, 'owner_user_id'), false)
})

test('503 is a service problem, not a request for project permission', () => {
  const message = planningErrorMessage(new PlanningApiError('http', 503))
  assert.match(message, /project service is unavailable/i)
  assert.doesNotMatch(message, /ask.*administrator|request.*access/i)
  assert.match(planningErrorMessage(new PlanningApiError('http', 403)), /permission/i)
})

test('public health sends neither a bearer nor a service key', async () => {
  let call
  const result = await api(async (url, init) => { call = { url, init }; return json({ status: 'healthy' }) }).checkPlanningHealth()
  assert.equal(result.status, 'healthy')
  assert.equal(call.url, 'https://planning.example.test/api/planning/health')
  assert.deepEqual(call.init.headers, { Accept: 'application/json' })
})

for (const method of Object.keys(requests)) {
  test(`${method} sends the account/admin bearer with private transport and no browser service key`, async () => {
    let call
    const result = await requests[method](api(async (url, init) => { call = { url, init }; return json(responseFor(method)) }))
    assert.deepEqual(result, responseFor(method))
    assert.equal(call.init.headers.Authorization, `Bearer ${(method === 'assign' ? admin() : user()).access_token}`)
    assert.equal(call.init.headers['X-API-Key'], undefined)
    assert.equal(call.init.credentials, 'omit')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(call.init.redirect, 'error')
    assert.equal(call.init.referrerPolicy, 'no-referrer')
    assert.equal(call.url.includes(user().access_token), false)
    if (!['list', 'create'].includes(method)) assert.ok(call.url.includes(encodeURIComponent(project)))
    if (['create', 'update'].includes(method)) assert.deepEqual(JSON.parse(call.init.body), buildPlanningRequest(form))
    if (method === 'assign') assert.deepEqual(JSON.parse(call.init.body), { username: 'alice' })
  })

  test(`${method} preserves safe HTTP status errors without consuming error bodies`, async () => {
    for (const status of [401, 403, 404, 409, 422, 500, 503]) {
      let read = false
      await assert.rejects(requests[method](api(async () => ({ ok: false, status, json() { read = true; throw new Error('private project data and credentials') } }))), error => {
        assert.equal(error.kind, 'http')
        assert.equal(error.status, status)
        assert.equal(planningErrorMessage(error).includes('private project data'), false)
        return true
      })
      assert.equal(read, false)
    }
  })

  test(`${method} refuses redirects, malformed JSON, wrong content type, and foreign response URLs`, async () => {
    for (const response of [
      { ok: true, status: 200, redirected: true },
      new Response('not JSON', { headers: { 'Content-Type': 'application/json' } }),
      new Response(JSON.stringify(responseFor(method)), { headers: { 'Content-Type': 'text/html' } }),
      { ok: true, status: 200, url: 'https://other.example.test/private' },
    ]) await assert.rejects(requests[method](api(async () => response)), errorOf('response'))
  })
}

test('every browser project operation refuses missing sessions before transport', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json({}) })
  for (const call of [
    () => client.getAllSchedules(), () => client.getSchedule(project), () => client.createSchedule(form),
    () => client.updateSchedule(project, form), () => client.deleteSchedule(project), () => client.assignProjectOwner(project, 'alice'),
  ]) await assert.rejects(async () => call(), error => error.status === 401)
  assert.equal(calls, 0)
})

test('invalid, expired, or role-inconsistent users cannot make project requests', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json([]) })
  for (const session of [null, {}, user({ role: 'admin' }), user({ user_id: 'another-id' }), user({ access_token: jwt({ role: 'admin' }) }), user({ expires_at: clock / 1000 })]) {
    await assert.rejects(client.getAllSchedules({ session }), error => error.status === 401)
  }
  assert.equal(calls, 0)
})

test('regular users cannot assign ownership and owner usernames must be canonical', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json(ownership()) })
  assert.throws(() => client.assignProjectOwner(project, 'alice', { session: user() }), error => error.status === 403)
  for (const username of ['ALICE', '', 'ab', 'user name', 'admin/other']) {
    assert.throws(() => client.assignProjectOwner(project, username, { session: admin() }), errorOf('input'))
  }
  assert.equal(calls, 0)
})

test('list responses require valid schedules and no duplicate project IDs', async () => {
  for (const data of [{ schedules: [] }, [null], [schedule(), schedule()], [schedule({ project_id: null })], [schedule({ schedule: {} })]]) {
    await assert.rejects(api(async () => json(data)).getAllSchedules({ session: user() }), errorOf('response'))
  }
  assert.deepEqual(await api(async () => json([])).getAllSchedules({ session: user() }), [])
})

test('detail and mutation responses cannot substitute a foreign project', async () => {
  for (const method of ['get', 'create', 'update', 'delete', 'assign']) {
    const data = method === 'delete' ? { project_id: 'foreign', deleted: true } : method === 'assign' ? ownership({ project_id: 'foreign' }) : schedule({ project_id: 'foreign' })
    await assert.rejects(requests[method](api(async () => json(data))), errorOf('response'))
  }
})

test('assignment rejects a mismatched username, invalid UUID, or injected response fields', async () => {
  for (const data of [ownership({ owner_username: 'bob' }), ownership({ owner_user_id: 'invalid' }), ownership({ access_token: 'injected' })]) {
    await assert.rejects(api(async () => json(data)).assignProjectOwner(project, 'alice', { session: admin() }), errorOf('response'))
  }
})

test('invalid project identifiers and mismatched update payloads never reach transport', () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json({}) })
  for (const id of ['', '  ', null, '\u0000', '\ud800', 'x'.repeat(101)]) {
    assert.throws(() => client.getSchedule(id, { session: user() }), errorOf('input'))
  }
  assert.throws(() => client.updateSchedule(project, { ...form, project_id: 'other' }, { session: user() }), errorOf('input'))
  assert.equal(calls, 0)
})

test('safe configuration rejects credential-bearing, path-bearing, and nonlocal HTTP roots', () => {
  for (const baseUrl of ['http://public.example.test', 'https://user:secret@example.test', 'https://example.test/api', 'https://example.test?token=secret', 'https://example.test#token', '']) {
    assert.throws(() => createPlanningApi({ baseUrl, origin: 'https://app.example.test', fetchImpl: async () => json([]) }), errorOf('configuration'))
  }
})

test('the browser origin uses the Vite proxy by default', async () => {
  let url
  await createPlanningApi({ origin: 'http://localhost:5173', now: () => clock, fetchImpl: async value => { url = value; return json([]) } }).getAllSchedules({ session: user() })
  assert.equal(url, 'http://localhost:5173/api/planning/schedules')
})

test('pre-aborted project requests never start and abort reasons are not exposed', async () => {
  const controller = new AbortController()
  controller.abort('private details')
  let calls = 0
  await assert.rejects(api(async () => { calls += 1; return json([]) }).getAllSchedules({ session: user(), signal: controller.signal }), abortOf)
  assert.equal(calls, 0)
})

test('late responses after cancellation cannot be used', async () => {
  let resolve
  const controller = new AbortController()
  const pending = api(() => new Promise(done => { resolve = done })).getAllSchedules({ session: user(), signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, abortOf)
  resolve(json([schedule()]))
  await new Promise(done => setTimeout(done, 0))
})

test('timeouts bound transports that ignore abort', async () => {
  await assert.rejects(api(() => new Promise(() => {}), { timeoutMs: 5 }).getAllSchedules({ session: user() }), errorOf('timeout'))
})

test('expiry is checked again before private project responses are accepted', async () => {
  let now = clock
  const client = api(async () => { now = expiry * 1000; return json([schedule()]) }, { now: () => now })
  await assert.rejects(client.getAllSchedules({ session: user() }), error => error.status === 401)
})

test('generation uses its longer budget and reconciles a completed create without repeating POST', async () => {
  const saved = globalThis.setTimeout
  const budgets = []
  const calls = []
  globalThis.setTimeout = (callback, delay, ...args) => { budgets.push(delay); return saved(callback, 1, ...args) }
  try {
    const client = api(async (url, init) => {
      calls.push({ url, method: init.method })
      return init.method === 'POST' ? new Promise(() => {}) : json(schedule())
    })
    assert.deepEqual(await client.createSchedule(form, { session: user() }), schedule())
    assert.deepEqual(budgets, [180000, 30000])
    assert.deepEqual(calls.map(call => call.method), ['POST', 'GET'])
    assert.equal(calls[1].url, `https://planning.example.test/api/planning/schedule/${encodeURIComponent(project)}`)
  } finally { globalThis.setTimeout = saved }
})

test('an unfinished create warns about ambiguous completion and never blindly retries', async () => {
  const calls = []
  const client = api(async (_url, init) => {
    calls.push(init.method)
    return init.method === 'POST' ? new Promise(() => {}) : json({}, 404)
  }, { generationTimeoutMs: 5 })
  await assert.rejects(client.createSchedule(form, { session: user() }), error => {
    assert.equal(error.kind, 'generation_timeout')
    assert.ok(error.message.includes('may still finish'))
    return true
  })
  assert.deepEqual(calls, ['POST', 'GET'])
})

test('update timeouts do not report the old schedule as a successful recalculation', async () => {
  let calls = 0
  await assert.rejects(api(() => { calls += 1; return new Promise(() => {}) }, { generationTimeoutMs: 5 }).updateSchedule(project, form, { session: user() }), errorOf('generation_timeout'))
  assert.equal(calls, 1)
})

test('network and unexpected errors are sanitized', async () => {
  await assert.rejects(api(async () => { throw new Error('private connection credentials') }).getAllSchedules({ session: user() }), errorOf('network'))
  assert.equal(planningErrorMessage(new Error('private details')).includes('private details'), false)
  assert.equal(new PlanningApiError('http', 404).message, 'This project is unavailable to your account.')
})

test('Planning and Schedule routes are session-gated and their requests receive that session', async () => {
  const app = await readFile(new URL('../App.jsx', import.meta.url), 'utf8')
  for (const path of ['/planning', '/schedule', '/schedule/:projectId']) {
    assert.ok(app.includes(`<Route path="${path}" element={<ProjectWorkspacePage`))
  }
  const planning = await readFile(new URL('../pages/Planning.jsx', import.meta.url), 'utf8')
  const schedules = await readFile(new URL('../pages/Schedule.jsx', import.meta.url), 'utf8')
  assert.match(planning, /createSchedule\(form, \{ session \}\)/)
  assert.match(schedules, /getAllSchedules\(\{ session, signal: operation\.controller\.signal \}\)/)
  assert.equal(schedules.match(/getSchedule\(cleanId, \{ session, signal: operation\.controller\.signal \}\)/g)?.length, 1)
  assert.match(schedules, /deleteSchedule\(\s*schedule\.project_id,\s*\{ session, signal: operation\.controller\.signal \}/)
  assert.match(schedules, /use_llm: false,\s*\},\s*\{ session, signal: operation\.controller\.signal \}/)
  const client = await readFile(new URL('./planningApi.js', import.meta.url), 'utf8')
  assert.doesNotMatch(client, /VITE_PLANNING_API_KEY|X-API-Key|localStorage|sessionStorage|document\.cookie/)
})
