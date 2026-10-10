import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { ACCOUNT_JWT_AUDIENCE, AccountApiError, accountApi, accountErrorMessage, createAccountApi, isAccountSessionValid, validateAccountSession } from './accountApi.js'

const clock = 1800000000000
const expiry = clock / 1000 + 300
const userId = '1b5e7946-0f72-4a41-9c5e-4184c817790b'
const otherId = 'aa2db83e-3b6d-4998-b3c6-d605b4d9b30e'
const encode = value => btoa(Array.from(new TextEncoder().encode(JSON.stringify(value)), byte => String.fromCharCode(byte)).join('')).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
const jwt = (claims = {}, header = { alg: 'HS256', typ: 'JWT' }) => `${encode(header)}.${encode({ sub: 'alice', account_id: userId, role: 'user', exp: expiry, iat: clock / 1000 - 10, iss: 'apm', aud: 'apm-accounts', token_use: 'account_access', ...claims })}.c2lnbmF0dXJl`
const session = (role = 'user', overrides = {}) => ({ access_token: jwt({ role }), token_type: 'bearer', username: 'alice', user_id: userId, role, expires_at: expiry, ...overrides })
const registration = (role = 'user', overrides = {}) => ({ user_id: userId, username: 'alice', role, ...overrides })
const metadata = (role = 'user', overrides = {}) => ({ ...registration(role), expires_at: expiry, ...overrides })
const legacy = () => ({ username: '管理员😀', role: 'admin', token_type: 'bearer', expires_at: expiry, access_token: jwt({ sub: '管理员😀', role: 'admin', token_use: 'legacy_admin', account_id: undefined }) })
const code = 'Wr1qZ5dG2eN6vC8bP9xL0sA4tY7uH3kJfMiRoQzU_-a'
const invitation = (overrides = {}) => ({ invitation_code: code, username: 'alice', expires_at: expiry, ...overrides })
const credentials = { username: 'alice', password: '  private😀password\t ' }
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const api = (fetchImpl, options = {}) => createAccountApi({ baseUrl: 'https://accounts.example.test', fetchImpl, now: () => clock, ...options })
const errorOf = kind => ({ name: 'AccountApiError', kind })
const abortOf = { name: 'AbortError', message: 'Account request cancelled.' }
const calls = {
  signup: client => client.signup(credentials),
  adminSignup: client => client.adminSignup({ ...credentials, invitation_code: code }),
  login: client => client.login(credentials),
  verifySession: client => client.verifySession(session()),
  createAdminInvitation: client => client.createAdminInvitation(legacy(), { username: 'alice' }),
}
const responseFor = method => method === 'signup' ? json(registration(), 201) : method === 'adminSignup' ? json(registration('admin'), 201) : method === 'login' ? json(session()) : method === 'verifySession' ? json(metadata()) : json(invitation())
const callWithSignal = (client, method, signal) => method === 'verifySession' ? client.verifySession(session(), { signal }) : method === 'createAdminInvitation' ? client.createAdminInvitation(legacy(), { username: 'alice' }, { signal }) : client[method]({ ...credentials, invitation_code: code }, { signal })

// Offline fakes only: no running backend, secrets, or runtime accounts.
test('lazy API exposes only the five intended operations', () => {
  assert.deepEqual(Object.keys(accountApi).sort(), Object.keys(calls).sort())
})

test('safe shared URL defaults, HTTPS overrides and exact loopbacks', async () => {
  for (const [options, root] of [
    [{}, 'http://127.0.0.1:8000'],
    [{ origin: 'https://workspace.example.test' }, 'https://workspace.example.test'],
    [{ origin: 'http://localhost:5173' }, 'http://localhost:5173'],
    [{ baseUrl: 'https://backend.example.test:8443/', origin: 'https://workspace.example.test' }, 'https://backend.example.test:8443'],
    [{ baseUrl: 'http://[::1]:8000' }, 'http://[::1]:8000'],
  ]) {
    let target
    await createAccountApi({ now: () => clock, fetchImpl: async url => { target = url; return json(session()) }, ...options }).login(credentials)
    assert.equal(target, `${root}/api/accounts/login`)
  }
})

test('unsafe roots and configurations reject before sending credentials or bearer', () => {
  let count = 0
  for (const baseUrl of ['', null, '/api', '//evil.test', 'https://user:pass@evil.test', 'https://@evil.test', 'https://good.test/api', 'https://good.test/./', 'https://good.test?', 'https://good.test#', 'https://good.test\\api', ' https://good.test', 'https://good.test\n', 'http://external.test', 'http://192.168.1.2', 'javascript:secret', 'data:secret']) {
    assert.throws(() => api(async () => { count++; return json(session()) }, { baseUrl }), errorOf('configuration'))
  }
  for (const origin of ['http://external.test', 'http://192.168.1.2:5173']) assert.throws(() => createAccountApi({ origin }), errorOf('configuration'))
  for (const timeoutMs of [0, -1, NaN, Infinity, 1.5, 2147483648, '10']) assert.throws(() => api(() => {}, { timeoutMs }), errorOf('configuration'))
  for (const options of [{ fetchImpl: null }, { now: null }, { audience: '' }, { issuer: 123 }]) assert.throws(() => api(() => {}, options), errorOf('configuration'))
  assert.equal(count, 0)
})

test('signup projects contract keys, preserves passwords and returns no token', async () => {
  let call
  const result = await api(async (url, init) => { call = { url, init }; return json(registration(), 201) }).signup({ ...credentials, role: 'admin', invitation_code: code, access_token: 'secret', headers: {} })
  assert.equal(call.url, 'https://accounts.example.test/api/accounts/signup')
  assert.equal(call.init.method, 'POST')
  assert.deepEqual(JSON.parse(call.init.body), credentials)
  assert.deepEqual(call.init.headers, { Accept: 'application/json', 'Content-Type': 'application/json' })
  assert.deepEqual(result, registration())
  assert.ok(Object.isFrozen(result))
  assert.equal(Object.hasOwn(result, 'access_token'), false)
})

test('admin signup sends only username, exact password and invitation, with no role', async () => {
  let body
  const result = await api(async (url, init) => { assert.ok(url.endsWith('/api/accounts/admin-signup')); body = JSON.parse(init.body); return json(registration('admin'), 201) }).adminSignup({ ...credentials, invitation_code: code, role: 'user' })
  assert.deepEqual(body, { ...credentials, invitation_code: code })
  assert.deepEqual(result, registration('admin'))
})

test('all operations omit cookies/cache/referrer and reject redirects; bearer is header-only', async () => {
  for (const method of Object.keys(calls)) {
    let call
    await calls[method](api(async (url, init) => { call = { url, init }; return responseFor(method) }))
    assert.equal(call.init.credentials, 'omit')
    assert.equal(call.init.cache, 'no-store')
    assert.equal(call.init.redirect, 'error')
    assert.equal(call.init.referrerPolicy, 'no-referrer')
    assert.ok(call.init.signal instanceof AbortSignal)
    assert.equal(call.url.includes(code), false)
    assert.equal(call.url.includes(credentials.password), false)
    if (['verifySession', 'createAdminInvitation'].includes(method)) assert.match(call.init.headers.Authorization, /^Bearer /)
    else assert.equal(Object.hasOwn(call.init.headers, 'Authorization'), false)
  }
})

test('canonical 3–50 ASCII usernames and exact Unicode code-point password limits', async () => {
  for (const password of ['😀'.repeat(12), '😀'.repeat(256), `x${' '.repeat(11)}`, `${' '.repeat(255)}x`, ` \t${'😀'.repeat(252)}\n `]) {
    let body
    await api(async (_url, init) => { body = JSON.parse(init.body); return json(registration(), 201) }).signup({ username: 'alice', password })
    assert.equal(body.password, password)
  }
  for (const username of ['abc', 'a'.repeat(50), 'a._-9', '0._']) {
    await api(async () => json(registration('user', { username }), 201)).signup({ username, password: credentials.password })
  }
  let count = 0
  const client = api(async () => { count++; return json(registration(), 201) })
  for (const username of ['', 'ab', 'a'.repeat(51), 'Alice', ' alice', 'alice ', 'ali ce', '管理員', 'a\nlice', 'a/b', 'a@b', null]) {
    await assert.rejects(client.signup({ username, password: credentials.password }), errorOf('input'))
  }
  for (const password of ['', '😀'.repeat(11), 'a'.repeat(11), '😀'.repeat(257), 'a'.repeat(257), null]) {
    await assert.rejects(client.signup({ username: 'alice', password }), errorOf('input'))
  }
  assert.equal(count, 0)
  let body
  await api(async (_url, init) => { body = JSON.parse(init.body); return json(session()) }).login({ username: 'alice', password: ' ' })
  assert.equal(body.password, ' ')
})

test('leading punctuation is rejected by every account input and session validator', async () => {
  let count = 0
  const client = api(async () => { count++; return json({}) })
  for (const username of ['.alice', '_alice', '-alice', '...', '__a', '-0a']) {
    for (const method of ['signup', 'adminSignup', 'login']) {
      await assert.rejects(client[method]({ ...credentials, username, invitation_code: code }), errorOf('input'))
    }
    await assert.rejects(client.createAdminInvitation(legacy(), { username }), errorOf('input'))
    const current = session('user', { username, access_token: jwt({ sub: username }) })
    assert.throws(() => validateAccountSession(current, { now: clock }), errorOf('response'))
    assert.equal(isAccountSessionValid(current, clock), false)
    await assert.rejects(client.verifySession(current), errorOf('response'))
  }
  assert.equal(count, 0)
})

test('both signup paths reject blank or ill-formed Unicode passwords without changing valid spaces', async () => {
  const controlsAndBom = '\u001c\u001d\u001e\u001f\ufeff'
  const badPasswords = [' '.repeat(12), ' '.repeat(256), '\t\n\r '.repeat(4), '\u00a0\u2003\u3000'.repeat(4), '\u0085'.repeat(12), '\ufeff'.repeat(12), ...Array.from(controlsAndBom, character => character.repeat(12)), `${controlsAndBom}\u0085\t `.repeat(2), '\ud800'.repeat(12), '\udfff'.repeat(12), `valid-password\ud800`, `\udfffvalid-password`, `valid\ud800x\udc00password`, `valid\udc00\ud800password`]
  for (const method of ['signup', 'adminSignup']) {
    let count = 0
    const client = api(async () => { count++; return responseFor(method) })
    for (const password of badPasswords) {
      await assert.rejects(client[method]({ username: 'alice', password, invitation_code: code }), errorOf('input'))
    }
    assert.equal(count, 0)
    for (const password of [' \tkeep spaces😀\n ', `\u00a0${'😀'.repeat(10)}\u3000`, ` ${'😀'.repeat(254)} `, `${controlsAndBom}😀${controlsAndBom}\u0085`, `${controlsAndBom}x${'\ufeff'.repeat(250)}`]) {
      let body
      await api(async (_url, init) => { body = JSON.parse(init.body); return responseFor(method) })[method]({ username: 'alice', password, invitation_code: code })
      assert.equal(body.password, password)
      assert.equal(Array.from(body.password).length, Array.from(password).length)
    }
  }
})

test('signup form uses the same Python whitespace-plus-BOM union as the API', async () => {
  const auth = await readFile(new URL('../pages/AccountAuth.jsx', import.meta.url), 'utf8')
  const expression = auth.match(/signup && !\/(.+?)\/u\.test\(input\.value\)/)
  assert.ok(expression)
  const apiSource = await readFile(new URL('./accountApi.js', import.meta.url), 'utf8')
  assert.ok(apiSource.includes(`!/${expression[1]}/u.test(input.password)`))
  const nonblank = new RegExp(expression[1], 'u')
  const whitespace = '\t\n\v\f\r \u001c\u001d\u001e\u001f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
  for (const character of whitespace) assert.equal(nonblank.test(character.repeat(12)), false)
  assert.equal(nonblank.test(whitespace), false)
  for (const character of ['x', '😀', '\u200b', '\u0000']) {
    assert.equal(nonblank.test(`${whitespace}${character}${whitespace}`), true)
  }
})

test('registration rejects tokens, wrong roles, mismatched users, invalid UUIDs and wrong status', async () => {
  for (const method of ['signup', 'adminSignup']) {
    const role = method === 'signup' ? 'user' : 'admin'
    for (const data of [null, [], {}, registration(role, { role: role === 'user' ? 'admin' : 'user' }), registration(role, { username: 'other' }), registration(role, { username: 'ALICE' }), registration(role, { user_id: 'not-a-uuid' }), registration(role, { access_token: 'secret' }), registration(role, { token_type: 'bearer' }), registration(role, { token: 'secret' })]) {
      await assert.rejects(calls[method](api(async () => json(data, 201))), errorOf('response'))
    }
    await assert.rejects(calls[method](api(async () => json(registration(role), 200))), errorOf('response'))
  }
})

test('login preserves server user/admin roles and freezes validated session', async () => {
  for (const role of ['user', 'admin']) {
    const current = await api(async () => json(session(role))).login(credentials)
    assert.equal(current.role, role)
    assert.equal(current.user_id, userId)
    assert.ok(Object.isFrozen(current))
    assert.ok(isAccountSessionValid(current, clock))
    assert.deepEqual(validateAccountSession(current, { now: clock, expectedUsername: 'ALICE' }), current)
  }
})

async function withAudienceConfig(value) {
  // Simulate Vite's public define substitution entirely in memory, without
  // loading env files or needing a bundler/server for these offline tests.
  const source = await readFile(new URL('./accountApi.js', import.meta.url), 'utf8')
  assert.equal(source.split('import.meta.env?.VITE_ACCOUNT_JWT_AUDIENCE').length, 2)
  const configured = source
    .replace('import.meta.env?.VITE_ACCOUNT_JWT_AUDIENCE', JSON.stringify(value) ?? 'undefined')
    .replace("'./adminApi.js'", JSON.stringify(new URL('./adminApi.js', import.meta.url).href))
  return import(`data:text/javascript;charset=utf-8,${encodeURIComponent(configured)}`)
}

test('public audience configuration is shared by factory, lazy client and validation helpers', async () => {
  assert.equal(ACCOUNT_JWT_AUDIENCE, 'apm-accounts')
  const defaultModule = await withAudienceConfig(undefined)
  assert.equal(defaultModule.ACCOUNT_JWT_AUDIENCE, 'apm-accounts')
  assert.ok(defaultModule.isAccountSessionValid(session(), clock))
  const configured = await withAudienceConfig('workspace-accounts')
  assert.equal(configured.ACCOUNT_JWT_AUDIENCE, 'workspace-accounts')
  const current = session('user', { access_token: jwt({ aud: 'workspace-accounts' }) })
  assert.deepEqual(configured.validateAccountSession(current, { now: clock }), current)
  assert.equal(configured.isAccountSessionValid(current, clock), true)
  assert.equal(configured.isAccountSessionValid(session(), clock), false)
  assert.throws(() => configured.validateAccountSession(session(), { now: clock }), errorOf('response'))
  const client = configured.createAccountApi({ baseUrl: 'https://accounts.example.test', now: () => clock, fetchImpl: async url => json(url.endsWith('/session') ? metadata() : current) })
  assert.deepEqual(await client.login(credentials), current)
  assert.deepEqual(await client.verifySession(current), metadata())
  await assert.rejects(configured.createAccountApi({ now: () => clock, fetchImpl: async () => json(session()) }).login(credentials), errorOf('response'))
  assert.equal(configured.isAccountSessionValid(session(), clock, { audience: 'apm-accounts' }), true)
  assert.deepEqual(await configured.createAccountApi({ now: () => clock, audience: 'apm-accounts', fetchImpl: async () => json(session()) }).login(credentials), session())

  const now = Math.floor(Date.now() / 1000)
  const liveExpiry = now + 300
  const live = session('user', { expires_at: liveExpiry, access_token: jwt({ aud: 'workspace-accounts', iat: now - 1, exp: liveExpiry }) })
  const liveMetadata = metadata('user', { expires_at: liveExpiry })
  const savedFetch = globalThis.fetch
  const targets = []
  try {
    globalThis.fetch = async url => { targets.push(url); return json(url.endsWith('/session') ? liveMetadata : live) }
    assert.deepEqual(await configured.accountApi.login(credentials), live)
    assert.deepEqual(await configured.accountApi.verifySession(live), liveMetadata)
    assert.deepEqual(configured.validateAccountSession(live), live)
    assert.equal(configured.isAccountSessionValid(live), true)
    globalThis.fetch = async () => json(session('user', { expires_at: liveExpiry, access_token: jwt({ iat: now - 1, exp: liveExpiry }) }))
    await assert.rejects(configured.accountApi.login(credentials), errorOf('response'))
  } finally { globalThis.fetch = savedFetch }
  assert.deepEqual(targets.map(url => new URL(url).pathname), ['/api/accounts/login', '/api/accounts/session'])
})

test('invalid configured audiences fail closed instead of silently falling back', async () => {
  for (const value of ['', '   ', 123]) {
    const configured = await withAudienceConfig(value)
    let count = 0
    assert.throws(() => configured.createAccountApi({ fetchImpl: async () => { count++; return json(session()) } }), errorOf('configuration'))
    assert.equal(configured.isAccountSessionValid(session(), clock), false)
    assert.throws(() => configured.validateAccountSession(session(), { now: clock }), errorOf('response'))
    assert.equal(count, 0)
  }
})

test('JWT claim purpose, account identity, subject, role, times, issuer and audience are checked', () => {
  const badClaims = [{ token_use: undefined }, { token_use: 'admin_access' }, { token_use: 'invitation' }, { account_id: undefined }, { account_id: otherId }, { account_id: 'invalid' }, { sub: 'ALICE' }, { sub: 'other' }, { role: 'admin' }, { exp: expiry + 1 }, { iat: undefined }, { iat: -1 }, { iat: clock / 1000 + 1 }, { iat: 1.5 }, { iss: '' }, { iss: null }, { aud: 'wrong' }, { aud: ['apm-accounts'] }]
  for (const claims of badClaims) {
    assert.throws(() => validateAccountSession(session('user', { access_token: jwt(claims) }), { now: clock }), errorOf('response'))
  }
  const custom = session('user', { access_token: jwt({ aud: 'custom', iss: 'trusted' }) })
  assert.ok(validateAccountSession(custom, { now: clock, audience: 'custom', issuer: 'trusted' }))
  assert.throws(() => validateAccountSession(custom, { now: clock, audience: 'custom', issuer: 'wrong' }), errorOf('response'))
})

test('malformed JWT, media fields, UUIDs and expiry never become sessions', () => {
  const invalidUtf8 = '_w'
  for (const overrides of [{ access_token: 'secret' }, { access_token: `e30.${invalidUtf8}.c2ln` }, { access_token: jwt({}, { alg: 'none' }) }, { access_token: jwt({}, { alg: 'HS256', typ: 'not-JWT' }) }, { access_token: 'x'.repeat(8193) }, { username: 'ALICE' }, { username: '管理员' }, { user_id: '00000000-0000-0000-0000-000000000000' }, { role: 'owner' }, { token_type: 'Bearer' }, { expires_at: '1800000300' }, { expires_at: Infinity }, { expires_at: 1.5 }]) {
    assert.throws(() => validateAccountSession(session('user', overrides), { now: clock }), errorOf('response'))
  }
  assert.throws(() => validateAccountSession(session(), { now: expiry * 1000 }), { ...errorOf('expired'), status: 401 })
  assert.equal(isAccountSessionValid(session(), expiry * 1000), false)
  assert.equal(isAccountSessionValid(null, clock), false)
})

test('protected session response binds all four fields and returns no token', async () => {
  const current = session('admin')
  let call
  const result = await api(async (url, init) => { call = { url, init }; return json(metadata('admin')) }).verifySession(current)
  assert.equal(call.url, 'https://accounts.example.test/api/accounts/session')
  assert.equal(call.init.method, 'GET')
  assert.equal(Object.hasOwn(call.init, 'body'), false)
  assert.deepEqual(call.init.headers, { Accept: 'application/json', Authorization: `Bearer ${current.access_token}` })
  assert.deepEqual(result, metadata('admin'))
  assert.ok(Object.isFrozen(result))
  for (const data of [null, {}, metadata('admin', { role: 'user' }), metadata('admin', { username: 'other' }), metadata('admin', { user_id: otherId }), metadata('admin', { expires_at: expiry + 1 }), metadata('admin', { access_token: current.access_token }), metadata('admin', { token_type: 'bearer' })]) {
    await assert.rejects(api(async () => json(data)).verifySession(current), errorOf('response'))
  }
})

test('invitation accepts legacy Unicode admin and managed admin, never user or absent session', async () => {
  for (const current of [legacy(), session('admin')]) {
    let call
    const result = await api(async (url, init) => { call = { url, init }; return json(invitation(), 201) }).createAdminInvitation(current, { username: 'alice', role: 'admin', invitation_code: 'ignored' })
    assert.equal(call.url, 'https://accounts.example.test/api/accounts/admin-invitations')
    assert.deepEqual(JSON.parse(call.init.body), { username: 'alice' })
    assert.equal(call.init.headers.Authorization, `Bearer ${current.access_token}`)
    assert.deepEqual(result, invitation())
    assert.ok(Object.isFrozen(result))
  }
  let count = 0
  const client = api(async () => { count++; return json(invitation()) })
  for (const current of [session(), null, undefined, {}, { ...legacy(), role: 'user' }, { ...legacy(), expires_at: clock / 1000 }]) {
    await assert.rejects(client.createAdminInvitation(current, { username: 'alice' }), { ...errorOf('expired'), status: 401 })
  }
  await assert.rejects(client.createAdminInvitation(legacy(), { username: 'Alice' }), errorOf('input'))
  assert.equal(count, 0)
})

test('invitation rejects malformed/weak codes, mismatched usernames, tokens and past/unsafe expiry', async () => {
  const badCodes = ['', 'short', 'a'.repeat(43), 'abcd'.repeat(12), 'x'.repeat(257), `${code} `, `<script>${code}</script>`, `https://secret.test/${code}`, null]
  for (const invitation_code of badCodes) {
    await assert.rejects(api(async () => json(invitation({ invitation_code }))).createAdminInvitation(legacy(), { username: 'alice' }), errorOf('response'))
    await assert.rejects(api(async () => json(registration('admin'), 201)).adminSignup({ ...credentials, invitation_code }), errorOf('input'))
  }
  for (const data of [null, {}, invitation({ username: 'other' }), invitation({ username: 'ALICE' }), invitation({ expires_at: clock / 1000 }), invitation({ expires_at: '1800000300' }), invitation({ expires_at: Infinity }), invitation({ expires_at: 8640000000001 }), invitation({ access_token: 'secret' })]) {
    await assert.rejects(api(async () => json(data)).createAdminInvitation(legacy(), { username: 'alice' }), errorOf('response'))
  }
})

for (const method of Object.keys(calls)) {
  test(`${method}: HTTP errors are safe, never read bodies or expose exception text`, async () => {
    for (const status of [400, 401, 403, 409, 422, 429, 500, 503]) {
      let read = false
      try {
        await calls[method](api(async () => ({ ok: false, status, json: () => { read = true; throw new Error('private-password') } })))
        assert.fail('must reject')
      } catch (error) {
        assert.ok(error instanceof AccountApiError)
        assert.equal(error.kind, 'http')
        assert.equal(error.status, status)
        if (status === 503) assert.equal(accountErrorMessage(error), 'The account service is unavailable. Ask the administrator to check the backend and account database setup, then try again.')
        assert.doesNotMatch(accountErrorMessage(error, { login: method === 'login' }), /private-password|Wr1q/)
      }
      assert.equal(read, false)
    }
    await assert.rejects(calls[method](api(async () => { throw new Error('private network detail') })), errorOf('network'))
  })

  test(`${method}: pre-aborted and ignored-abort fetch cannot produce late success`, async () => {
    const controller = new AbortController()
    controller.abort('private abort reason')
    let count = 0
    await assert.rejects(callWithSignal(api(async () => { count++; return responseFor(method) }), method, controller.signal), abortOf)
    assert.equal(count, 0)
    const active = new AbortController()
    let resolve
    let signal
    const pending = callWithSignal(api((_url, init) => { signal = init.signal; return new Promise(done => { resolve = done }) }), method, active.signal)
    active.abort('secret')
    await assert.rejects(pending, abortOf)
    assert.equal(signal.aborted, true)
    resolve(responseFor(method))
    await new Promise(done => setTimeout(done, 0))
  })

  test(`${method}: abort and timeout also bound ignored-abort body readers`, async () => {
    for (const timeout of [false, true]) {
      const controller = new AbortController()
      let resolveBody
      let started
      let signal
      const ready = new Promise(done => { started = done })
      const pending = callWithSignal(api((_url, init) => {
        signal = init.signal
        return Promise.resolve({ ok: true, status: ['signup', 'adminSignup'].includes(method) ? 201 : 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => { started(); return new Promise(done => { resolveBody = done }) } })
      }, { timeoutMs: timeout ? 10 : 30000 }), method, controller.signal)
      await ready
      if (!timeout) controller.abort('private reason')
      await assert.rejects(pending, timeout ? errorOf('timeout') : abortOf)
      assert.equal(signal.aborted, true)
      resolveBody(await responseFor(method).json())
      await new Promise(done => setTimeout(done, 0))
    }
  })

  test(`${method}: timeout bounds a fetch that ignores abort`, async () => {
    let signal
    await assert.rejects(calls[method](api((_url, init) => { signal = init.signal; return new Promise(() => {}) }, { timeoutMs: 10 })), errorOf('timeout'))
    assert.equal(signal.aborted, true)
  })
}

test('cooperative fetch abort and spontaneous AbortError remain sanitized cancellation', async () => {
  const controller = new AbortController()
  const pending = api((_url, init) => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('secret', 'AbortError')), { once: true }))).login(credentials, { signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, abortOf)
  await assert.rejects(api(async () => { throw new DOMException('private', 'AbortError') }).login(credentials), abortOf)
})

test('malformed JSON, media types, redirects and mismatched success URLs are rejected', async () => {
  for (const response of [
    new Response('invalid JSON', { headers: { 'Content-Type': 'application/json' } }),
    new Response(JSON.stringify(session()), { headers: { 'Content-Type': 'text/html' } }),
    { ok: true, redirected: true }, { ok: true, type: 'opaqueredirect' }, { ok: true, url: 'https://evil.test/api/accounts/login' },
  ]) await assert.rejects(api(async () => response).login(credentials), errorOf('response'))
  let read = false
  await assert.rejects(api(async () => ({ ok: false, status: 302, json: () => { read = true } })).login(credentials), { ...errorOf('http'), status: 302 })
  assert.equal(read, false)
  assert.equal(accountErrorMessage(new Error('private secret')), 'The server returned an invalid or mismatched account response. The response was not used.')
  assert.equal(accountErrorMessage(new AccountApiError('http', 401), { login: true }), 'Invalid username or password.')
})

test('protected sessions and login/invitation output are rechecked after body completion', async () => {
  for (const method of ['login', 'verifySession', 'createAdminInvitation']) {
    let time = clock
    const client = api(async () => { const response = responseFor(method); time = expiry * 1000; return response }, { now: () => time })
    await assert.rejects(calls[method](client), { ...errorOf('expired'), status: 401 })
  }
})

test('account transport and UI never use persistence, logging, clipboard or code-bearing URLs', async () => {
  const keys = ['localStorage', 'sessionStorage', 'indexedDB', 'document', 'navigator']
  const saved = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  try {
    for (const key of keys) Object.defineProperty(globalThis, key, { configurable: true, get() { throw new Error('Persistence forbidden') } })
    for (const method of Object.keys(calls)) await calls[method](api(async () => responseFor(method)))
  } finally {
    keys.forEach((key, index) => { if (saved[index]) Object.defineProperty(globalThis, key, saved[index]); else delete globalThis[key] })
  }
  for (const path of ['./accountApi.js', './signInApi.js', '../pages/AccountAuth.jsx']) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|console\.(?:log|warn|error|debug)|navigator\.clipboard/)
    assert.doesNotMatch(source, /(?:href|to)=\{[^}]*invitation_code/)
  }
})

test('UI contracts retain native validation, UTF-16 limits, canonical hints and request guards', async () => {
  const auth = await readFile(new URL('../pages/AccountAuth.jsx', import.meta.url), 'utf8')
  for (const name of ['UserLogin', 'UserSignup']) assert.match(auth, new RegExp(`export function ${name}\\(`))
  assert.doesNotMatch(auth, /AdminSignup|adminSignup|invitation_code|\/admin\/(?:login|signup)|useNavigate|useLocation/)
  assert.match(auth, /name="password"[\s\S]*?maxLength=\{512\}/)
  assert.match(auth, /name="confirmation"[\s\S]*?maxLength=\{512\}/)
  assert.match(auth, /Array\.from\(input\.value\)\.length/)
  assert.match(auth, /reportValidity\(\)/)
  assert.match(auth, /setCustomValidity/)
  assert.doesNotMatch(auth, /noValidate|password\.trim\(|name="role"/)
  assert.match(auth, /toLowerCase\(\)/)
  const pattern = auth.match(/pattern=\{signup \? ("[^"\n]+") : undefined\}/)
  assert.ok(pattern)
  const usernamePattern = new RegExp(`^${JSON.parse(pattern[1])}$`, 'v')
  for (const username of ['alice', 'a.b-c_d', 'a'.repeat(50)]) assert.equal(usernamePattern.test(username), true)
  for (const username of ['ALICE', 'ab', '.alice', 'álîce', 'a'.repeat(51)]) assert.equal(usernamePattern.test(username), false)
  assert.match(auth, /minLength=\{signup \? 3 : 1\} maxLength=\{signup \? 50 : 200\}/)
  assert.match(auth, /setUsername\(signup \? event\.target\.value\.toLowerCase\(\) : event\.target\.value\)/)
  assert.match(auth, /Array\.from\(input\.value\.trim\(\)\)/)
  assert.match(auth, /starting with a letter or digit/)
  assert.ok(auth.includes('signup && !/[^\\s\\p{White_Space}\\u001c-\\u001f]/u.test(input.value)'))
  assert.match(auth, /signup && !wellFormedUnicode\(input\.value\)/)
  assert.match(auth, /await accountApi\.signup\(input, options\)/)
  assert.match(auth, /await signIn\(input, options\)/)
  assert.match(auth, /signup \? accountErrorMessage\(failure\) : signInErrorMessage\(failure\)/)
  assert.doesNotMatch(auth, /accountApi\.login|accountApi\.verifySession|role:\s*["']admin/)
  assert.match(auth, /namedItem\("password"\)\.value !== password/)
  assert.match(auth, /namedItem\("confirmation"\)\.value !== confirmation/)
  assert.match(auth, /function guardPasswordPaste[\s\S]*?event\.preventDefault\(\)/)
  assert.equal((auth.match(/onPaste=\{guardPasswordPaste\}/g) || []).length, 2)
  assert.match(auth, /sequence === sequence && !controller\.signal\.aborted/)
  assert.match(auth, /current\.controller\?\.abort\(\)/)

})
