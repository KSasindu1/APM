import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { AdminApiError, adminErrorMessage, createAdminApi, isAdminSessionValid, resolveAdminApiUrl, validateAdminKnowledgeResponse, validateAdminSession } from './adminApi.js'

const clock = 1800000000000
const expiry = clock / 1000 + 300
const encode = value => btoa(Array.from(new TextEncoder().encode(JSON.stringify(value)), byte => String.fromCharCode(byte)).join('')).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
const jwt = (claims = {}, header = { alg: 'HS256', typ: 'JWT' }) => `${encode(header)}.${encode({ sub: 'admin', role: 'admin', exp: expiry, ...claims })}.c2lnbmF0dXJl`
const session = (overrides = {}) => ({ access_token: jwt(), token_type: 'bearer', username: 'admin', role: 'admin', expires_at: expiry, ...overrides })
const metadata = (overrides = {}) => ({ username: 'admin', role: 'admin', expires_at: expiry, ...overrides })
const credentials = { username: 'admin', password: 'private-password' }
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const api = (fetchImpl, options = {}) => createAdminApi({ baseUrl: 'https://admin.example.test', fetchImpl, now: () => clock, ...options })
const errorOf = kind => ({ name: 'AdminApiError', kind })
const abortOf = { name: 'AbortError', message: 'Admin request cancelled.' }
const pendingTransport = (_url, init) => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('secret transport detail', 'AbortError')), { once: true }))

test('managed account admins accept typed ASCII case only with purpose, UUID and canonical username', async () => {
  const accountId = '1b5e7946-0f72-4a41-9c5e-4184c817790b'
  const managed = session({ access_token: jwt({ token_use: 'account_access', account_id: accountId }) })
  assert.equal(validateAdminSession(managed, { now: clock, expectedUsername: 'AdMiN' }).username, 'admin')
  assert.equal((await api(async () => json(managed)).login({ ...credentials, username: 'ADMIN' })).username, 'admin')
  for (const claims of [
    { token_use: 'legacy_admin', account_id: accountId },
    { account_id: accountId },
    { token_use: 'account_access' },
    { token_use: 'account_access', account_id: 'not-a-uuid' },
    { token_use: 'account_access', account_id: '00000000-0000-0000-0000-000000000000' },
    { token_use: 'account_access', account_id: accountId, sub: 'ADMIN' },
  ]) {
    assert.throws(() => validateAdminSession(session({ access_token: jwt(claims) }), { now: clock, expectedUsername: 'ADMIN' }), errorOf('response'))
  }
  assert.throws(() => validateAdminSession(managed, { now: clock, expectedUsername: 'other' }), errorOf('response'))
  assert.throws(() => validateAdminSession(managed, { now: clock, expectedUsername: ' admin ' }), errorOf('response'))
  const unicode = session({ username: 'Ädmin', access_token: jwt({ sub: 'Ädmin', token_use: 'account_access', account_id: accountId }) })
  assert.throws(() => validateAdminSession(unicode, { now: clock, expectedUsername: 'ÄDMIN' }), errorOf('response'))
  for (const username of ['.admin', '_admin', '-admin']) {
    const noncanonical = session({ username, access_token: jwt({ sub: username, token_use: 'account_access', account_id: accountId }) })
    assert.throws(() => validateAdminSession(noncanonical, { now: clock }), errorOf('response'))
    assert.throws(() => validateAdminSession(noncanonical, { now: clock, expectedUsername: username }), errorOf('response'))
    assert.throws(() => validateAdminSession(noncanonical, { now: clock, expectedUsername: username.toUpperCase() }), errorOf('response'))
    await assert.rejects(api(async () => json(noncanonical)).login({ ...credentials, username: username.toUpperCase() }), errorOf('response'))
  }
})

test('legacy administrator identities retain exact case and UTF-8 matching', async () => {
  for (const username of ['Admin', 'Ädmin', '管理员😀', '𠮷'.repeat(100), '.Admin', '_Admin', '-Admin']) {
    const current = session({ username, access_token: jwt({ sub: username }) })
    assert.equal(validateAdminSession(current, { now: clock, expectedUsername: username }).username, username)
    assert.equal((await api(async () => json(current)).login({ username, password: credentials.password })).username, username)
    if (username.toLowerCase() !== username) {
      assert.throws(() => validateAdminSession(current, { now: clock, expectedUsername: username.toLowerCase() }), errorOf('response'))
      await assert.rejects(api(async () => json(current)).login({ username: username.toLowerCase(), password: credentials.password }), errorOf('response'))
    }
  }
  assert.throws(() => validateAdminSession(session(), { now: clock, expectedUsername: 'ADMIN' }), errorOf('response'))
})

// All requests below use local fakes; none contact a server.
test('default uses the browser origin, or the shared backend in Node', () => {
  assert.equal(resolveAdminApiUrl({ origin: 'http://localhost:5173' }), 'http://localhost:5173')
  assert.equal(resolveAdminApiUrl({ origin: 'https://apm.example.test' }), 'https://apm.example.test')
  assert.throws(() => resolveAdminApiUrl({ origin: 'http://192.168.1.10:5173' }), errorOf('configuration'))
  assert.equal(resolveAdminApiUrl({ origin: undefined }), 'http://127.0.0.1:8000')
})

test('default client reaches backend 8000 without a browser or explicit override', async () => {
  let url
  await createAdminApi({ now: () => clock, fetchImpl: async target => { url = target; return json(session()) } }).login(credentials)
  assert.equal(url, 'http://127.0.0.1:8000/api/auth/login')
})

test('default client uses the current browser origin for login and protected requests', async () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'location')
  const calls = []
  try {
    Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://apm.example.test' } })
    const client = createAdminApi({ now: () => clock, fetchImpl: async url => { calls.push(url); return json(url.endsWith('/login') ? session() : url.endsWith('/session') ? metadata() : {}) } })
    const current = await client.login(credentials)
    await client.verifySession(current)
    await client.request('/api/risk/knowledge/documents', current)
    assert.deepEqual(calls, ['https://apm.example.test/api/auth/login', 'https://apm.example.test/api/auth/session', 'https://apm.example.test/api/risk/knowledge/documents'])
  } finally {
    if (saved) Object.defineProperty(globalThis, 'location', saved)
    else delete globalThis.location
  }
})

test('explicit server-root override wins and normalizes the trailing slash', async () => {
  let url
  await api(async target => { url = target; return json(session()) }, { baseUrl: 'https://backend.example.test:8443/', origin: 'http://localhost:5173' }).login(credentials)
  assert.equal(url, 'https://backend.example.test:8443/api/auth/login')
  for (const local of ['http://localhost:8000', 'http://127.0.0.1:8000/', 'http://[::1]:8000']) {
    assert.equal(resolveAdminApiUrl({ configuredUrl: local }), local.replace(/\/$/, ''))
  }
})

for (const baseUrl of [
  '', null, 'relative', '/api', '//backend.example.test', 'https://admin:password@backend.example.test',
  'https://@backend.example.test', 'https://backend.example.test?secret=token', 'https://backend.example.test?',
  'https://backend.example.test/#token', 'https://backend.example.test/#', 'https://backend.example.test/api',
  'https://backend.example.test/api/..', 'https://backend.example.test/./', 'https://backend.example.test\\api',
  ' https://backend.example.test', 'https://backend.example.test\n', 'http://external.example.test',
  'javascript:alert(1)', 'data:text/plain,password', 'file:///tmp/api', 'ftp://backend.example.test',
]) {
  test(`unsafe URL rejected before credentials or bearer can reach fetch: ${String(baseUrl).replace(/\n/g, '<newline>')}`, () => {
    let calls = 0
    assert.throws(() => api(async () => { calls += 1; return json(session()) }, { baseUrl, origin: 'https://apm.example.test' }), errorOf('configuration'))
    assert.equal(calls, 0)
  })
}

test('non-loopback HTTP roots are rejected even when defaulting to the same browser origin', () => {
  let calls = 0
  const fetchImpl = async () => { calls += 1; return json(session()) }
  for (const origin of ['http://intranet.example.test:5173', 'http://192.168.1.10:5173']) {
    assert.throws(() => resolveAdminApiUrl({ origin }), errorOf('configuration'))
    assert.throws(() => createAdminApi({ origin, fetchImpl }).login(credentials), errorOf('configuration'))
    assert.throws(() => createAdminApi({ origin, fetchImpl }).verifySession(session()), errorOf('configuration'))
    assert.throws(() => createAdminApi({ origin, fetchImpl }).request('/api/risk', session()), errorOf('configuration'))
  }
  assert.equal(calls, 0)
})

test('explicit same-origin plaintext HTTP is rejected before credentials or bearer are sent', () => {
  let calls = 0
  for (const origin of ['http://intranet.example.test:5173', 'http://192.168.1.10:5173']) {
    assert.throws(() => resolveAdminApiUrl({ configuredUrl: `${origin}/`, origin }), errorOf('configuration'))
    assert.throws(() => api(async () => { calls += 1; return json({}) }, { baseUrl: `${origin}/`, origin }).login(credentials), errorOf('configuration'))
    assert.throws(() => api(async () => { calls += 1; return json({}) }, { baseUrl: origin, origin }).verifySession(session()), errorOf('configuration'))
  }
  assert.equal(calls, 0)
})

test('login sends only JSON credentials, without bearer, cookies, cache, referrer, or redirect following', async () => {
  let call
  const result = await api(async (url, init) => { call = { url, init }; return json(session({ ignored: 'not retained' })) }).login(credentials)
  assert.equal(call.url, 'https://admin.example.test/api/auth/login')
  assert.equal(call.init.method, 'POST')
  assert.deepEqual(call.init.headers, { Accept: 'application/json', 'Content-Type': 'application/json' })
  assert.deepEqual(JSON.parse(call.init.body), credentials)
  assert.equal(call.init.credentials, 'omit')
  assert.equal(call.init.cache, 'no-store')
  assert.equal(call.init.redirect, 'error')
  assert.equal(call.init.referrerPolicy, 'no-referrer')
  assert.ok(call.init.signal instanceof AbortSignal)
  assert.equal(call.url.includes(credentials.password), false)
  assert.equal(Object.hasOwn(result, 'ignored'), false)
  assert.ok(Object.isFrozen(result))
})

test('100-point usernames and 256-point passwords accept supplementary Unicode characters', async () => {
  const username = '𠮷'.repeat(100)
  const password = '😀'.repeat(256)
  const current = session({ username, access_token: jwt({ sub: username }) })
  const calls = []
  const client = api(async (url, init) => {
    calls.push({ url, init })
    return json(url.endsWith('/login') ? current : metadata({ username }))
  })
  const result = await client.login({ username, password })
  assert.deepEqual(JSON.parse(calls[0].init.body), { username, password })
  assert.equal(result.username, username)
  assert.ok(isAdminSessionValid(result, clock))
  await client.verifySession(result)
  assert.equal(calls.length, 2)
})

test('credential limits reject over-limit code points before transport', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json(session()) })
  for (const username of ['a'.repeat(101), '𠮷'.repeat(101), `a${'𠮷'.repeat(100)}`]) {
    await assert.rejects(client.login({ username, password: 'valid' }), errorOf('response'))
    assert.throws(() => validateAdminSession(session({ username, access_token: jwt({ sub: username }) }), { now: clock }), errorOf('response'))
  }
  for (const password of ['a'.repeat(257), '😀'.repeat(257), `a${'😀'.repeat(256)}`]) {
    await assert.rejects(client.login({ username: 'admin', password }), errorOf('response'))
  }
  assert.equal(calls, 0)
})

test('password whitespace and Unicode are sent exactly, including at the code-point limit', async () => {
  for (const password of [' '.repeat(256), ` \t${'😀'.repeat(252)}\n `, '  unchanged password  ']) {
    let body
    await api(async (_url, init) => { body = JSON.parse(init.body); return json(session()) }).login({ username: 'admin', password })
    assert.equal(body.password, password)
  }
})

test('C0 and DEL username controls are rejected before transport', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json(session()) })
  for (const code of [...Array.from({ length: 32 }, (_, index) => index), 127]) {
    const username = `ad${String.fromCharCode(code)}min`
    await assert.rejects(client.login({ username, password: 'valid' }), errorOf('response'))
  }
  assert.equal(calls, 0)
})

test('login input DOM limits accommodate two UTF-16 units per code point', async () => {
  const source = await readFile(new URL('../admin/AdminLogin.jsx', import.meta.url), 'utf8')
  assert.match(source, /id="admin-username"[\s\S]*?maxLength=\{200\}/)
  assert.match(source, /id="admin-password"[\s\S]*?maxLength=\{512\}/)
  assert.doesNotMatch(source, /password\.trim\(/)
})

test('protected session GET binds identity and expiry, and sends bearer only in the header', async () => {
  let call
  const current = session()
  const result = await api(async (url, init) => { call = { url, init }; return json(metadata()) }).verifySession(current)
  assert.equal(call.url, 'https://admin.example.test/api/auth/session')
  assert.equal(call.init.method, 'GET')
  assert.deepEqual(call.init.headers, { Accept: 'application/json', Authorization: `Bearer ${current.access_token}` })
  assert.equal(Object.hasOwn(call.init, 'body'), false)
  assert.deepEqual(result, metadata())
  assert.equal(Object.hasOwn(result, 'access_token'), false)
  assert.equal(call.url.includes(current.access_token), false)
  assert.ok(Object.isFrozen(result))
})

test('risk GET/JSON POST use safe transport, and caller transport/header overrides cannot leak credentials', async () => {
  const calls = []
  const client = api(async (url, init) => { calls.push({ url, init }); return json({ results: [] }) })
  await client.request('/api/risk/knowledge/documents?project_id=p%2Fone', session(), { headers: { Authorization: 'attacker' }, credentials: 'include', redirect: 'follow', cache: 'default' })
  await client.request('/api/risk/knowledge/search', session(), { method: 'POST', body: { project_id: 'p/one', query: 'question', top_k: 5 } })
  assert.equal(calls[0].url, 'https://admin.example.test/api/risk/knowledge/documents?project_id=p%2Fone')
  assert.equal(calls[0].init.headers.Authorization, `Bearer ${session().access_token}`)
  assert.equal(calls[0].init.headers['Content-Type'], undefined)
  assert.deepEqual(JSON.parse(calls[1].init.body), { project_id: 'p/one', query: 'question', top_k: 5 })
  for (const call of calls) {
    assert.equal(call.init.credentials, 'omit')
    assert.equal(call.init.redirect, 'error')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(call.url.includes(session().access_token), false)
  }
})

test('indexed document DELETE uses the protected transport without a body or credential overrides', async () => {
  let call
  const data = { project_id: 'p/one', document_id: 'doc-one', deleted_chunks: 3 }
  const result = await api(async (url, init) => { call = { url, init }; return json(data) }).request(
    '/api/risk/knowledge/documents/doc-one?project_id=p%2Fone', session(),
    { method: 'DELETE', headers: { Authorization: 'attacker' }, credentials: 'include', redirect: 'follow' },
  )
  assert.deepEqual(result, data)
  assert.equal(call.url, 'https://admin.example.test/api/risk/knowledge/documents/doc-one?project_id=p%2Fone')
  assert.equal(call.init.method, 'DELETE')
  assert.equal(call.init.headers.Authorization, `Bearer ${session().access_token}`)
  assert.equal(call.init.headers['Content-Type'], undefined)
  assert.equal(Object.hasOwn(call.init, 'body'), false)
  assert.equal(call.init.credentials, 'omit')
  assert.equal(call.init.cache, 'no-store')
  assert.equal(call.init.redirect, 'error')
  assert.equal(call.init.referrerPolicy, 'no-referrer')
})

test('DELETE is limited to indexed document routes and refuses bodies and invalid sessions before transport', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json({}) })
  for (const path of ['/api/risk', '/api/risk/documents', '/api/risk/knowledge/documents', '/api/risk/knowledge/documents/', '/api/risk/knowledge/documents/doc/chunks', '/api/risk/knowledge/search']) {
    await assert.rejects(client.request(path, session(), { method: 'DELETE' }), errorOf('configuration'))
  }
  const path = '/api/risk/knowledge/documents/doc?project_id=one'
  for (const method of ['PUT', 'PATCH', 'delete']) {
    await assert.rejects(client.request(path, session(), { method }), errorOf('configuration'))
  }
  await assert.rejects(client.request(path, session(), { method: 'DELETE', body: {} }), errorOf('configuration'))
  await assert.rejects(client.request(path, session({ role: 'user' }), { method: 'DELETE' }), AdminApiError)
  await assert.rejects(client.request(path, session({ expires_at: clock / 1000 }), { method: 'DELETE' }), AdminApiError)
  assert.equal(calls, 0)
})

test('DELETE preserves cancellation and safe HTTP errors without reading their bodies', async () => {
  const path = '/api/risk/knowledge/documents/doc?project_id=one'
  for (const status of [401, 403, 500]) {
    let bodyRead = false
    const client = api(async () => ({ ok: false, status, json: () => { bodyRead = true; throw new Error('private detail') } }))
    await assert.rejects(client.request(path, session(), { method: 'DELETE' }), error => error.kind === 'http' && error.status === status)
    assert.equal(bodyRead, false)
  }
  const controller = new AbortController()
  controller.abort()
  let calls = 0
  await assert.rejects(api(async () => { calls += 1; return json({}) }).request(path, session(), { method: 'DELETE', signal: controller.signal }), abortOf)
  assert.equal(calls, 0)
})

test('multipart uploads leave boundary/content-type to fetch', async () => {
  let call
  const body = new FormData()
  body.append('file', new Blob(['offline-pdf'], { type: 'application/pdf' }), 'test.pdf')
  await api(async (_url, init) => { call = init; return json({ result: { chunks_created: 1 } }) }).request('/api/risk/documents?project_id=one', session(), { method: 'POST', body })
  assert.equal(call.body, body)
  assert.equal(call.headers['Content-Type'], undefined)
  assert.equal(call.headers.Authorization, `Bearer ${session().access_token}`)
})

test('private request destinations stay confined to risk routes', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json({}) })
  for (const path of ['https://evil.example.test/api/risk', '//evil.example.test/api/risk', '/api/auth/login', '/api/risk/../auth/session', '/api/risk/%2e%2e/auth/session', '/api/risk-other', '/api/risk#secret', '/api/risk\\..\\auth']) {
    await assert.rejects(client.request(path, session()), errorOf('configuration'))
  }
  await assert.rejects(client.request('/api/risk', session(), { method: 'DELETE' }), errorOf('configuration'))
  await assert.rejects(client.request('/api/risk', session(), { body: {} }), errorOf('configuration'))
  assert.equal(calls, 0)
})

test('valid sessions are copied/frozen and exact expiry is expired', () => {
  const current = session()
  assert.notEqual(validateAdminSession(current, { now: clock }), current)
  assert.ok(isAdminSessionValid(current, clock))
  assert.equal(isAdminSessionValid(current, expiry * 1000), false)
  assert.throws(() => validateAdminSession(current, { now: expiry * 1000 }), { ...errorOf('expired'), status: 401 })
})

test('malformed login fields, token format, claims, and TTL are rejected without echoing secrets', async () => {
  const malformed = [null, [], {},
    session({ access_token: '' }), session({ access_token: 'opaque-token' }), session({ access_token: 'x.y.z\r\nAuthorization: secret' }),
    session({ access_token: 'x.y.z' }), session({ access_token: jwt({}, { alg: 'none' }) }),
    session({ access_token: jwt({ role: 'user' }) }), session({ access_token: jwt({ sub: 'other' }) }), session({ access_token: jwt({ exp: expiry + 1 }) }),
    session({ access_token: jwt({ exp: String(expiry) }) }), session({ token_type: 'Bearer' }), session({ role: 'user' }),
    session({ username: '' }), session({ username: ' admin' }), session({ username: 'admin\n' }), session({ username: 42 }),
    session({ expires_at: String(expiry) }), session({ expires_at: expiry + 0.5 }), session({ expires_at: Infinity }), session({ expires_at: Number.MAX_SAFE_INTEGER }),
    session({ expires_at: clock / 1000, access_token: jwt({ exp: clock / 1000 }) }),
  ]
  for (const data of malformed) {
    await assert.rejects(api(async () => json(data)).login(credentials), error => {
      assert.ok(error instanceof AdminApiError)
      assert.ok(['response', 'expired'].includes(error.kind))
      assert.equal(error.message.includes(credentials.password), false)
      assert.equal(error.message.includes(session().access_token), false)
      return true
    })
  }
})

test('login response identity must match submitted username', async () => {
  await assert.rejects(api(async () => json(session({ username: 'other', access_token: jwt({ sub: 'other' }) }))).login(credentials), errorOf('response'))
})

test('session endpoint cannot change username, role, expiry or return another token', async () => {
  for (const data of [null, [], {}, metadata({ username: 'other' }), metadata({ role: 'user' }), metadata({ expires_at: expiry + 1 }), metadata({ expires_at: String(expiry) }), metadata({ access_token: jwt() })]) {
    await assert.rejects(api(async () => json(data)).verifySession(session()), errorOf('response'))
  }
})

test('invalid credentials and invalid/expired sessions are rejected before fetch', async () => {
  let calls = 0
  const client = api(async () => { calls += 1; return json({}) })
  for (const input of [{ username: '', password: 'secret' }, { username: 'admin', password: '' }, { username: 'admin', password: 123 }, { username: 'admin', password: 'a'.repeat(257) }]) {
    await assert.rejects(client.login(input), errorOf('response'))
  }
  for (const current of [null, {}, session({ role: 'user' }), session({ access_token: 'Bearer token' }), session({ access_token: jwt({ exp: clock / 1000 }), expires_at: clock / 1000 })]) {
    await assert.rejects(client.verifySession(current), AdminApiError)
    await assert.rejects(client.request('/api/risk/knowledge/documents', current), AdminApiError)
  }
  assert.equal(calls, 0)
})

for (const operation of ['login', 'session', 'risk']) {
  const execute = client => operation === 'login' ? client.login(credentials) : operation === 'session' ? client.verifySession(session()) : client.request('/api/risk/knowledge/documents', session())
  test(`${operation}: HTTP 401 vs 403 stay distinct; raw HTTP bodies are never read`, async () => {
    for (const status of [401, 403, 422, 500]) {
      let bodyRead = false
      const client = api(async () => ({ ok: false, status, json: () => { bodyRead = true; throw new Error('password/token detail') } }))
      await assert.rejects(execute(client), error => {
        assert.equal(error.kind, 'http')
        assert.equal(error.status, status)
        const message = adminErrorMessage(error, { login: operation === 'login' })
        assert.equal(message.includes('Invalid username or password'), operation === 'login' && status === 401)
        assert.equal(message.includes('password/token detail'), false)
        if (status === 403) assert.match(message, /denied/)
        return true
      })
      assert.equal(bodyRead, false)
    }
  })
  test(`${operation}: network errors stay network errors with safe detail`, async () => {
    await assert.rejects(execute(api(async () => { throw new Error('private-password token raw network detail') })), error => {
      assert.equal(error.kind, 'network')
      assert.equal(error.status, 0)
      assert.equal(adminErrorMessage(error, { login: true }).includes('Invalid username'), false)
      assert.equal(error.message.includes('private-password'), false)
      return true
    })
  })
}

test('malformed JSON/media type, redirected and mismatched URL successes are refused', async () => {
  for (const response of [
    new Response('{bad', { headers: { 'Content-Type': 'application/json' } }),
    new Response(JSON.stringify(session()), { headers: { 'Content-Type': 'text/html' } }),
    { ok: true, redirected: true, json: () => session() },
    { ok: true, type: 'opaqueredirect' },
    { ok: true, url: 'https://other.example.test/api/auth/login' },
  ]) {
    await assert.rejects(api(async () => response).login(credentials), errorOf('response'))
  }
})

test('bare 302 never follows or reads a redirect body', async () => {
  let calls = 0
  await assert.rejects(api(async (_url, init) => { calls += 1; assert.equal(init.redirect, 'error'); return new Response(null, { status: 302, headers: { Location: 'https://evil.example.test' } }) }).login(credentials), { ...errorOf('http'), status: 302 })
  assert.equal(calls, 1)
})

test('pre-aborted login, session and risk never start transport or expose abort reasons', async () => {
  const controller = new AbortController()
  controller.abort('private-password abort detail')
  let calls = 0
  const client = api(async () => { calls += 1; return json(session()) })
  await assert.rejects(client.login(credentials, { signal: controller.signal }), abortOf)
  await assert.rejects(client.verifySession(session(), { signal: controller.signal }), abortOf)
  await assert.rejects(client.request('/api/risk', session(), { signal: controller.signal }), abortOf)
  assert.equal(calls, 0)
})

test('abort propagates to active fetch and is not labelled a network failure', async () => {
  const controller = new AbortController()
  let transportSignal
  const pending = api((url, init) => { transportSignal = init.signal; return pendingTransport(url, init) }).login(credentials, { signal: controller.signal })
  controller.abort(new Error('secret abort reason'))
  await assert.rejects(pending, abortOf)
  assert.equal(transportSignal.aborted, true)
})

test('transport AbortError is sanitized and remains cancellation', async () => {
  await assert.rejects(api(async () => { throw new DOMException('secret', 'AbortError') }).verifySession(session()), abortOf)
})

test('timeout aborts a cooperative fetch with safe error metadata', async () => {
  let signal
  await assert.rejects(api((url, init) => { signal = init.signal; return pendingTransport(url, init) }, { timeoutMs: 10 }).login(credentials), { ...errorOf('timeout'), status: 0 })
  assert.equal(signal.aborted, true)
})

test('timeout also rejects an ignored-abort transport before its late login response', async () => {
  let resolve
  const pending = api(() => new Promise(done => { resolve = done }), { timeoutMs: 10 }).login(credentials)
  await assert.rejects(pending, errorOf('timeout'))
  resolve(json(session()))
  await new Promise(done => setTimeout(done, 0))
})

test('timeout bounds a fetch that ignores abort', async () => {
  await assert.rejects(api(() => new Promise(() => {}), { timeoutMs: 10 }).verifySession(session()), errorOf('timeout'))
})

test('timeout includes response body reading, even when the reader ignores abort', async () => {
  await assert.rejects(api(async () => ({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => new Promise(() => {}) }), { timeoutMs: 10 }).login(credentials), errorOf('timeout'))
})

test('aborted fetch that ignores its signal cannot return a late login/session/private success', async () => {
  for (const operation of ['login', 'session', 'risk']) {
    const controller = new AbortController()
    let resolve
    const client = api(() => new Promise(done => { resolve = done }))
    const pending = operation === 'login' ? client.login(credentials, { signal: controller.signal }) : operation === 'session' ? client.verifySession(session(), { signal: controller.signal }) : client.request('/api/risk', session(), { signal: controller.signal })
    controller.abort()
    await assert.rejects(pending, abortOf)
    resolve(json(operation === 'login' ? session() : operation === 'session' ? metadata() : {}))
    await new Promise(done => setTimeout(done, 0))
  }
})

test('aborting during body read prevents a late parsed login success', async () => {
  const controller = new AbortController()
  let resolveBody
  let bodyStarted
  const started = new Promise(done => { bodyStarted = done })
  const pending = api(async () => ({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => { bodyStarted(); return new Promise(done => { resolveBody = done }) } })).login(credentials, { signal: controller.signal })
  await started
  controller.abort()
  await assert.rejects(pending, abortOf)
  resolveBody(session())
  await new Promise(done => setTimeout(done, 0))
})

test('expiry is checked again after login, session and protected response completion', async () => {
  for (const operation of ['login', 'session', 'risk']) {
    let time = clock
    const client = api(async () => { time = expiry * 1000; return json(operation === 'login' ? session() : operation === 'session' ? metadata() : {}) }, { now: () => time })
    const pending = operation === 'login' ? client.login(credentials) : operation === 'session' ? client.verifySession(session()) : client.request('/api/risk', session())
    await assert.rejects(pending, { ...errorOf('expired'), status: 401 })
  }
})

test('invalid timeout configuration cannot initiate requests', () => {
  for (const timeoutMs of [0, -1, Infinity, 1.5, 2147483648]) assert.throws(() => api(async () => json({}), { timeoutMs }), errorOf('configuration'))
})

const project = 'project-one'
const indexedDocument = overrides => ({ document_id: 'document-one', project_id: project, name: 'Project.pdf', source: 'Project.pdf', document_hash: null, chunks_count: 1, ...overrides })
const documentsResponse = overrides => ({ project_id: project, total_documents: 1, total_chunks: 1, documents: [indexedDocument()], ...overrides })
const storedChunk = overrides => ({ chunk_id: 'project-one:document-one:0', chunk_index: 0, text: 'Stored text', metadata: { project_id: project, document_id: 'document-one' }, ...overrides })
const chunksResponse = overrides => ({ project_id: project, document_id: 'document-one', total_chunks: 1, chunks: [storedChunk()], ...overrides })
const searchResult = overrides => ({ knowledge_type: 'document', source: 'Project.pdf', chunk_id: 'project-one:document-one:0', score: 0.5, content: 'Matching content', metadata: { project_id: project }, ...overrides })
const searchResponse = overrides => ({ project_id: project, results: [searchResult()], ...overrides })
function assertInvalidKnowledge(data, collection, documentId) {
  assert.throws(() => validateAdminKnowledgeResponse(data, project, collection, documentId), error => {
    assert.equal(error.name, 'AdminApiError')
    assert.equal(error.kind, 'response')
    assert.equal(error.status, 0)
    assert.equal(adminErrorMessage(error), 'The server returned an invalid or mismatched admin response. The response was not used.')
    return true
  })
}

test('knowledge validator accepts current documents, chunks, document search and Planning records', () => {
  const responses = [
    [documentsResponse(), 'documents'],
    [chunksResponse(), 'chunks', 'document-one'],
    [searchResponse({ results: [searchResult(), { knowledge_type: 'planning_agent', source: 'PostgreSQL:tasks', record_id: '42', content: 'Planning text', metadata: { project_id: project, table: 'tasks', record_id: '42' } }] }), 'results'],
  ]
  for (const [data, collection, documentId] of responses) assert.equal(validateAdminKnowledgeResponse(data, project, collection, documentId), data)
})

test('legacy document hashes, source fallback and unassigned chunks do not impose false count correlations', () => {
  const oldDocument = { document_id: 'legacy-document', name: 'Unknown document', chunks_count: 1 }
  const data = documentsResponse({ total_chunks: 3, documents: [oldDocument] })
  assert.equal(validateAdminKnowledgeResponse(data, project, 'documents'), data)
  const nullable = documentsResponse({ documents: [indexedDocument({ document_hash: null, source: null })] })
  assert.equal(validateAdminKnowledgeResponse(nullable, project, 'documents'), nullable)
})

test('legacy chunks accept string indexes, nullable/empty text and empty metadata', () => {
  for (const chunk of [storedChunk({ chunk_index: '2', text: null, metadata: {} }), storedChunk({ text: '', metadata: null }), storedChunk({ text: undefined, metadata: undefined })]) {
    const data = chunksResponse({ chunks: [chunk] })
    assert.equal(validateAdminKnowledgeResponse(data, project, 'chunks', 'document-one'), data)
  }
})

test('search accepts optional nullable fields, Planning record IDs and finite scores outside zero-to-one', () => {
  for (const result of [
    searchResult({ score: -0.25 }), searchResult({ score: 1.25 }),
    { record_id: 'legacy-record', content: '', metadata: {} },
    { chunk_id: null, record_id: 'planning-record', knowledge_type: null, source: null, content: null, score: null, metadata: null },
    { content: '', metadata: {} },
  ]) {
    const data = searchResponse({ results: [result] })
    assert.equal(validateAdminKnowledgeResponse(data, project, 'results'), data)
  }
})

for (const field of ['total_documents', 'total_chunks']) {
  test(`documents reject non-count render values for ${field}`, () => {
    for (const value of [{ private: 'sensitive-detail' }, [], '1', null, undefined, true, -1, 1.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1]) {
      assertInvalidKnowledge(documentsResponse({ [field]: value }), 'documents')
    }
  })
}

for (const field of ['document_id', 'name', 'chunks_count', 'source', 'document_hash']) {
  test(`documents reject malformed ${field} before Dashboard state`, () => {
    for (const value of [{ private: 'sensitive-detail' }, ['not-a-field'], true]) {
      assertInvalidKnowledge(documentsResponse({ documents: [indexedDocument({ [field]: value })] }), 'documents')
    }
  })
}

test('documents reject missing required IDs/names/counts and invalid numeric counts', () => {
  for (const overrides of [{ document_id: '' }, { document_id: null }, { document_id: undefined }, { name: null }, { name: undefined }, { name: 42 }, { chunks_count: '1' }, { chunks_count: -1 }, { chunks_count: 0.5 }, { chunks_count: null }, { chunks_count: undefined }]) {
    assertInvalidKnowledge(documentsResponse({ documents: [indexedDocument(overrides)] }), 'documents')
  }
})

for (const field of ['chunk_id', 'chunk_index', 'text', 'metadata']) {
  test(`chunks reject malformed ${field} before Dashboard state`, () => {
    const values = field === 'metadata' ? [[], 'metadata', false] : [{ private: 'sensitive-detail' }, [], true]
    for (const value of values) assertInvalidKnowledge(chunksResponse({ chunks: [storedChunk({ [field]: value })] }), 'chunks', 'document-one')
  })
}

test('chunks reject invalid IDs, index counts, text numbers and total counts', () => {
  for (const overrides of [{ chunk_id: '' }, { chunk_id: null }, { chunk_id: undefined }, { chunk_index: -1 }, { chunk_index: 0.5 }, { chunk_index: null }, { chunk_index: undefined }, { text: 42 }]) {
    assertInvalidKnowledge(chunksResponse({ chunks: [storedChunk(overrides)] }), 'chunks', 'document-one')
  }
  for (const total_chunks of [{ private: 'sensitive-detail' }, '1', null, undefined, -1, 1.5, Infinity]) assertInvalidKnowledge(chunksResponse({ total_chunks }), 'chunks', 'document-one')
})

for (const field of ['chunk_id', 'record_id', 'knowledge_type', 'source', 'content', 'score', 'metadata']) {
  test(`search rejects malformed ${field} before Dashboard state`, () => {
    const values = field === 'metadata' ? [[], 'metadata', false] : [{ private: 'sensitive-detail' }, [], true]
    for (const value of values) assertInvalidKnowledge(searchResponse({ results: [searchResult({ [field]: value })] }), 'results')
  })
}

test('search rejects non-string IDs and non-finite/nonnumeric scores', () => {
  for (const overrides of [{ chunk_id: '' }, { chunk_id: 123 }, { record_id: 123 }, { score: '0.5' }, { score: Infinity }, { score: NaN }, { content: 123 }, { knowledge_type: 123 }, { source: 123 }]) {
    assertInvalidKnowledge(searchResponse({ results: [searchResult(overrides)] }), 'results')
  }
})

test('knowledge responses reject malformed collection items and root envelopes', () => {
  for (const [make, collection, documentId] of [[documentsResponse, 'documents'], [chunksResponse, 'chunks', 'document-one'], [searchResponse, 'results']]) {
    for (const root of [null, [], {}, { [collection]: [] }, make({ project_id: {} })]) assertInvalidKnowledge(root, collection, documentId)
    for (const item of [null, [], true, 'private-item']) assertInvalidKnowledge(make({ [collection]: [item] }), collection, documentId)
    for (const items of [null, {}, 'private-collection']) assertInvalidKnowledge(make({ [collection]: items }), collection, documentId)
  }
  assertInvalidKnowledge(documentsResponse(), 'unknown')
})

test('knowledge validator retains project and selected-document binding', () => {
  for (const [make, item, collection, documentId] of [[documentsResponse, indexedDocument, 'documents'], [chunksResponse, storedChunk, 'chunks', 'document-one'], [searchResponse, searchResult, 'results']]) {
    assertInvalidKnowledge(make({ project_id: 'other-project' }), collection, documentId)
    assertInvalidKnowledge(make({ [collection]: [item({ project_id: 'other-project' })] }), collection, documentId)
    assertInvalidKnowledge(make({ [collection]: [item({ metadata: { project_id: 'other-project' } })] }), collection, documentId)
  }
  assertInvalidKnowledge(chunksResponse({ document_id: 'other-document' }), 'chunks', 'document-one')
  assertInvalidKnowledge(chunksResponse({ chunks: [storedChunk({ document_id: 'other-document' })] }), 'chunks', 'document-one')
  assertInvalidKnowledge(chunksResponse({ chunks: [storedChunk({ metadata: { document_id: 'other-document' } })] }), 'chunks', 'document-one')
})

test('empty knowledge collections remain valid', () => {
  for (const [data, collection, documentId] of [[documentsResponse({ total_documents: 0, total_chunks: 0, documents: [] }), 'documents'], [chunksResponse({ total_chunks: 0, chunks: [] }), 'chunks', 'document-one'], [searchResponse({ results: [] }), 'results']]) {
    assert.equal(validateAdminKnowledgeResponse(data, project, collection, documentId), data)
  }
})

test('Dashboard validates all three response collections before updating private state', async () => {
  const source = await readFile(new URL('../admin/AdminDashboard.jsx', import.meta.url), 'utf8')
  assert.equal(source.match(/const data = validateAdminKnowledgeResponse\(await apiRequest/g)?.length, 3)
  assert.doesNotMatch(source, /function projectResponse/)
})

test('admin sessions never touch browser storage or cookies; app state remains memory-only', async () => {
  const keys = ['localStorage', 'sessionStorage', 'indexedDB', 'document']
  const saved = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  try {
    for (const key of keys) Object.defineProperty(globalThis, key, { configurable: true, get() { throw new Error('Persistence must not be accessed') } })
    const current = await api(async () => json(session())).login(credentials)
    await api(async () => json(metadata())).verifySession(current)
    await api(async () => json({ documents: [] })).request('/api/risk/knowledge/documents', current)
  } finally {
    keys.forEach((key, index) => {
      if (saved[index]) Object.defineProperty(globalThis, key, saved[index])
      else delete globalThis[key]
    })
  }
  for (const path of ['./adminApi.js', '../admin/AdminLogin.jsx', '../admin/AdminDashboard.jsx', '../App.jsx']) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/)
    assert.doesNotMatch(source, /console\.(?:log|warn|error|debug)/)
  }
})
