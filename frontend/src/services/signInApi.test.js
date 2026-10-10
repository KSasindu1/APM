import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
import { AccountApiError, createAccountApi } from './accountApi.js'
import { AdminApiError, createAdminApi } from './adminApi.js'
import { SignInError, createSignInApi, signIn, signInErrorMessage } from './signInApi.js'

const clock = 1800000000000
const expiry = clock / 1000 + 300
const userId = '1b5e7946-0f72-4a41-9c5e-4184c817790b'
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url')
const token = (username, role, account) => `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: username, role, exp: expiry, iat: clock / 1000 - 10, iss: 'apm', aud: 'apm-accounts', ...(account ? { account_id: userId, token_use: 'account_access' } : { token_use: 'legacy_admin' }) })}.c2lnbmF0dXJl`
const session = (username = 'alice', role = 'user', account = true) => ({ access_token: token(username, role, account), token_type: 'bearer', username, role, expires_at: expiry, ...(account ? { user_id: userId } : {}) })
const metadata = current => Object.fromEntries(Object.entries(current).filter(([key]) => !['access_token', 'token_type'].includes(key)))
const credentials = { username: ' Alice ', password: '  private😀password\t ' }
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const errorOf = (kind, status = 0) => ({ name: 'SignInError', kind, status })
const abortOf = { name: 'AbortError', message: 'Sign-in cancelled.' }
const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function harness({ accountLogin = async input => session(input.username), accountVerify = async current => metadata(current), adminLogin = async input => session(input.username, 'admin', false), adminVerify = async current => metadata(current), now = () => clock } = {}) {
  const calls = []
  const wrap = (stage, run) => async (input, options) => { calls.push({ stage, input, signal: options.signal }); return run(input, options) }
  const api = createSignInApi({
    accountClient: { login: wrap('account.login', accountLogin), verifySession: wrap('account.verify', accountVerify) },
    adminClient: { login: wrap('admin.login', adminLogin), verifySession: wrap('admin.verify', adminVerify) },
    now,
  })
  return { ...api, calls }
}
const stages = client => client.calls.map(call => call.stage)

// Offline fakes only: no running backend, credentials, or live accounts.
test('exports lazy signIn and a dependency-injectable factory', () => {
  assert.equal(typeof signIn, 'function')
  assert.deepEqual(Object.keys(createSignInApi()), ['signIn'])
  for (const options of [{ accountClient: {} }, { adminClient: null }, { now: 1 }]) assert.throws(() => createSignInApi(options), errorOf('configuration'))
})

test('canonical account attempt lowercases only ASCII username, projects keys, and verifies before returning', async () => {
  const ready = deferred()
  const started = deferred()
  const client = harness({ accountVerify: async current => { started.resolve(); return ready.promise.then(() => metadata(current)) } })
  const controller = new AbortController()
  let returned = false
  const pending = client.signIn({ ...credentials, role: 'admin', invitation_code: 'ignored', access_token: 'ignored' }, { signal: controller.signal }).then(result => { returned = true; return result })
  await started.promise
  assert.equal(returned, false)
  assert.deepEqual(stages(client), ['account.login', 'account.verify'])
  assert.deepEqual(client.calls[0].input, { username: 'alice', password: credentials.password })
  assert.ok(client.calls.every(call => call.signal === controller.signal))
  ready.resolve()
  const result = await pending
  assert.deepEqual(result, session())
  assert.equal(Object.isFrozen(result), true)
})

test('server roles, not literal usernames, determine the returned session', async () => {
  for (const [username, role] of [['admin', 'user'], ['ordinary.person', 'admin']]) {
    const client = harness({ accountLogin: async input => session(input.username, role) })
    const result = await client.signIn({ username, password: ' ' })
    assert.equal(result.role, role)
    assert.deepEqual(stages(client), ['account.login', 'account.verify'])
  }
})

for (const status of [401, 503]) test(`account-login HTTP ${status} alone enables verified admin fallback with original case`, async () => {
  const client = harness({ accountLogin: async () => { throw new AccountApiError('http', status) } })
  const controller = new AbortController()
  const result = await client.signIn(credentials, { signal: controller.signal })
  assert.deepEqual(stages(client), ['account.login', 'admin.login', 'admin.verify'])
  assert.deepEqual(client.calls[0].input, { username: 'alice', password: credentials.password })
  assert.deepEqual(client.calls[1].input, { username: 'Alice', password: credentials.password })
  assert.ok(client.calls.every(call => call.signal === controller.signal))
  assert.equal(result.username, 'Alice')
  assert.equal(result.role, 'admin')
})

test('admin fallback also accepts a canonical managed admin response', async () => {
  const client = harness({ accountLogin: async () => { throw new AccountApiError('http', 401) }, adminLogin: async () => session('alice', 'admin') })
  const result = await client.signIn(credentials)
  assert.equal(result.username, 'alice')
  assert.equal(result.role, 'admin')
  assert.deepEqual(stages(client), ['account.login', 'admin.login', 'admin.verify'])
})

test('noncanonical bootstrap usernames skip account login, preserving trimmed case and Unicode without normalization', async () => {
  for (const username of ['X', 'Ab', '.Root', 'Root Owner', '管理员😀', 'Kelvin', 'İAdmin', 'e\u0301', 'É', 'A'.repeat(51), '😀'.repeat(100)]) {
    const client = harness()
    const result = await client.signIn({ username: ` \t${username}\u3000`, password: ' exact ' })
    assert.deepEqual(stages(client), ['admin.login', 'admin.verify'])
    assert.equal(client.calls[0].input.username, username)
    assert.equal(result.username, username)
  }
  for (const username of ['A_1', '0.-', 'A'.repeat(50)]) {
    const client = harness()
    await client.signIn({ username, password: 'x' })
    assert.equal(client.calls[0].input.username, username.toLowerCase())
    assert.equal(client.calls[0].stage, 'account.login')
  }
})

test('passwords stay exact, including all spaces, at 1–256 code points on either route', async () => {
  for (const username of ['Alice', '管理员😀']) for (const password of [' ', '\t', '  secret  ', '\u001c\ufeff\u0085', '😀', '😀'.repeat(256), ` ${'😀'.repeat(254)} `]) {
    const client = harness()
    await client.signIn({ username, password })
    assert.equal(client.calls[0].input.password, password)
  }
  const fallback = harness({ accountLogin: async () => { throw new AccountApiError('http', 401) } })
  const password = ` ${'😀'.repeat(254)} `
  await fallback.signIn({ username: 'Alice', password })
  assert.equal(fallback.calls[0].input.password, password)
  assert.equal(fallback.calls[1].input.password, password)
})

test('invalid input is rejected before any request', async () => {
  const client = harness()
  for (const input of [undefined, null, {}, { username: 1, password: 'x' }, { username: 'Alice', password: 1 }, ...['', ' \t\u3000 ', '😀'.repeat(101), 'a'.repeat(101), 'bad\u0000name', 'bad\u007fname', 'bad\ud800name'].map(username => ({ username, password: 'x' })), ...['', 'x'.repeat(257), '😀'.repeat(257)].map(password => ({ username: 'Alice', password }))]) {
    await assert.rejects(client.signIn(input), errorOf('input'))
  }
  assert.deepEqual(client.calls, [])
})

test('configuration, response, network, timeout, expiry, forbidden and rate limit errors never trigger fallback', async () => {
  for (const error of [new AccountApiError('configuration'), new AccountApiError('response'), new AccountApiError('network'), new AccountApiError('timeout'), new AccountApiError('expired', 401), ...[400, 403, 429, 500, 502, 504].map(status => new AccountApiError('http', status)), new AdminApiError('http', 401), { kind: 'http', status: 401 }, new Error('password must never be exposed')]) {
    const client = harness({ accountLogin: async () => { throw error } })
    await assert.rejects(client.signIn(credentials), errorOf(error instanceof AccountApiError || error instanceof AdminApiError ? error.kind : 'response', error instanceof AccountApiError || error instanceof AdminApiError ? error.status : 0))
    assert.deepEqual(stages(client), ['account.login'])
  }
})

test('malformed account login output is never treated as eligible fallback', async () => {
  for (const candidate of [undefined, null, {}, session('someone-else'), { ...session(), role: 'owner' }, { ...session(), access_token: 'bad' }, { ...session(), expires_at: 1 }]) {
    const client = harness({ accountLogin: async () => candidate })
    await assert.rejects(client.signIn(credentials), errorOf('response'))
    assert.deepEqual(stages(client), ['account.login'])
  }
})

test('no fallback after any account verification failure, including HTTP 401/503', async () => {
  for (const error of [new AccountApiError('http', 401), new AccountApiError('http', 503), new AccountApiError('http', 403), new AccountApiError('http', 429), new AccountApiError('expired', 401), new AccountApiError('network'), new AccountApiError('timeout'), new AccountApiError('response')]) {
    const client = harness({ accountVerify: async () => { throw error } })
    await assert.rejects(client.signIn(credentials), errorOf(error.kind === 'http' && error.status === 401 ? 'expired' : error.kind, error.status))
    assert.deepEqual(stages(client), ['account.login', 'account.verify'])
  }
})

test('protected metadata must match every identity field and role before returning either session', async () => {
  for (const account of [true, false]) for (const change of [() => undefined, () => ({}), current => ({ ...metadata(current), role: account ? 'admin' : 'user' }), current => ({ ...metadata(current), username: 'someone-else' }), current => ({ ...metadata(current), expires_at: expiry + 1 }), ...(account ? [current => ({ ...metadata(current), user_id: 'wrong-id' })] : [])]) {
    const client = harness(account ? { accountVerify: async current => change(current) } : { adminVerify: async current => change(current) })
    await assert.rejects(client.signIn({ username: account ? 'Alice' : '管理员😀', password: 'x' }), errorOf('response'))
    assert.deepEqual(stages(client), account ? ['account.login', 'account.verify'] : ['admin.login', 'admin.verify'])
  }
})

test('admin errors, malformed admin candidates and verification failures never return a session', async () => {
  for (const error of [new AdminApiError('http', 401), new AdminApiError('http', 403), new AdminApiError('http', 429), new AdminApiError('network'), new AdminApiError('timeout'), new AdminApiError('response')]) {
    const client = harness({ adminLogin: async () => { throw error } })
    await assert.rejects(client.signIn({ username: '管理员', password: 'x' }), errorOf(error.kind, error.status))
    assert.deepEqual(stages(client), ['admin.login'])
  }
  const malformed = harness({ adminLogin: async () => session('管理员', 'user', false) })
  await assert.rejects(malformed.signIn({ username: '管理员', password: 'x' }), errorOf('response'))
  assert.deepEqual(stages(malformed), ['admin.login'])
  const failedVerification = harness({ adminVerify: async () => { throw new AdminApiError('http', 401) } })
  await assert.rejects(failedVerification.signIn({ username: '管理员', password: 'x' }), errorOf('expired', 401))
  assert.deepEqual(stages(failedVerification), ['admin.login', 'admin.verify'])
})

test('expiry is checked again after verification completes', async () => {
  for (const account of [true, false]) {
    let time = clock
    const verify = async current => { time = expiry * 1000; return metadata(current) }
    const client = harness({ now: () => time, ...(account ? { accountVerify: verify } : { adminVerify: verify }) })
    await assert.rejects(client.signIn({ username: account ? 'Alice' : '管理员', password: 'x' }), errorOf('expired', 401))
  }
})

test('already aborted and spontaneous aborts are sanitized without fallback', async () => {
  const controller = new AbortController()
  controller.abort('secret cancellation reason')
  const client = harness()
  await assert.rejects(client.signIn(credentials, { signal: controller.signal }), abortOf)
  assert.deepEqual(client.calls, [])
  const spontaneous = harness({ accountLogin: async () => { throw new DOMException('secret', 'AbortError') } })
  await assert.rejects(spontaneous.signIn(credentials), abortOf)
  assert.deepEqual(stages(spontaneous), ['account.login'])
})

test('cancellation races noncooperative clients at every stage and ignores late results', async () => {
  for (const stage of ['accountLogin', 'accountVerify', 'adminLogin', 'adminVerify']) {
    const ready = deferred()
    const started = deferred()
    const controller = new AbortController()
    const client = harness({ [stage]: async input => { started.resolve(); await ready.promise; return stage.endsWith('Verify') ? metadata(input) : session(input.username, stage.startsWith('admin') ? 'admin' : 'user', !stage.startsWith('admin')) } })
    const pending = client.signIn({ username: stage.startsWith('admin') ? '管理员😀' : 'Alice', password: 'x' }, { signal: controller.signal })
    await started.promise
    controller.abort('secret')
    await assert.rejects(pending, abortOf)
    const before = stages(client)
    ready.resolve()
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(stages(client), before)
    assert.ok(client.calls.every(call => call.signal === controller.signal))
  }
})

test('a late eligible account failure after abort cannot initiate admin login', async () => {
  for (const status of [401, 503]) {
    const ready = deferred()
    const controller = new AbortController()
    const client = harness({ accountLogin: async () => ready.promise })
    const pending = client.signIn(credentials, { signal: controller.signal })
    controller.abort()
    await assert.rejects(pending, abortOf)
    ready.reject(new AccountApiError('http', status))
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(stages(client), ['account.login'])
  }
})

test('independent requests do not share credentials, signals or verification state', async () => {
  const first = deferred()
  const started = deferred()
  const client = harness({ accountVerify: async current => { started.resolve(); await first.promise; return metadata(current) } })
  const controller = new AbortController()
  const pending = client.signIn(credentials, { signal: controller.signal })
  await started.promise
  const second = await client.signIn({ username: '管理员', password: 'different' })
  controller.abort()
  await assert.rejects(pending, abortOf)
  first.resolve()
  assert.equal(second.username, '管理员')
  assert.notEqual(client.calls[0].signal, client.calls[2].signal)
})

test('real injected clients use the chosen protected endpoint before releasing a session', async () => {
  for (const route of ['user', 'managed-admin', 'bootstrap-admin']) {
    const calls = []
    const current = route === 'bootstrap-admin' ? session('Alice', 'admin', false) : session('alice', route === 'user' ? 'user' : 'admin')
    const fetchImpl = async (url, init) => {
      calls.push({ path: new URL(url).pathname, init })
      if (url.endsWith('/api/accounts/login') && route === 'bootstrap-admin') return json({}, 401)
      return url.endsWith('/session') ? json(metadata(current)) : json(current)
    }
    const options = { baseUrl: 'https://example.test', fetchImpl, now: () => clock }
    const client = createSignInApi({ accountClient: createAccountApi(options), adminClient: createAdminApi(options), now: () => clock })
    const result = await client.signIn(credentials)
    assert.equal(result.role, current.role)
    assert.deepEqual(calls.map(call => call.path), route === 'bootstrap-admin' ? ['/api/accounts/login', '/api/auth/login', '/api/auth/session'] : ['/api/accounts/login', '/api/accounts/session'])
    assert.equal(calls.at(-1).init.headers.Authorization, `Bearer ${result.access_token}`)
    for (const call of calls.filter(call => call.init.body)) assert.equal(JSON.parse(call.init.body).password, credentials.password)
  }
})

test('real transport malformed/redirected account responses never enable fallback', async () => {
  for (const response of [json({}), new Response('not JSON', { headers: { 'Content-Type': 'text/plain' } }), { ok: false, status: 401, redirected: true }, { ok: false, status: 503, url: 'https://different.example.test' }]) {
    const paths = []
    const options = { baseUrl: 'https://example.test', now: () => clock, fetchImpl: async url => { paths.push(new URL(url).pathname); return response } }
    const client = createSignInApi({ accountClient: createAccountApi(options), adminClient: createAdminApi(options), now: () => clock })
    await assert.rejects(client.signIn(credentials), errorOf('response'))
    assert.deepEqual(paths, ['/api/accounts/login'])
  }
})

test('error mapping uses fixed safe messages, never exception contents', () => {
  const secret = 'private password or token'
  for (const error of [new Error(secret), { kind: 'http', status: 401, message: secret }, new SignInError('response'), new AccountApiError('http', 401), new AdminApiError('network')]) {
    error.message = secret
    assert.ok(signInErrorMessage(error))
    assert.ok(!signInErrorMessage(error).includes(secret))
  }
  assert.equal(signInErrorMessage(new SignInError('http', 401)), 'Invalid username or password.')
  assert.match(signInErrorMessage(new SignInError('http', 429)), /Too many requests/)
  assert.match(signInErrorMessage(new SignInError('expired', 401)), /expired/)
})

test('service never persists, logs, copies, or hardcodes administrator identities', async () => {
  const source = await readFile(new URL('./signInApi.js', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|console\.(?:log|warn|error|debug)|navigator\.clipboard|username\s*===\s*['"]admin['"]/)
  const keys = ['localStorage', 'sessionStorage', 'indexedDB', 'document', 'navigator']
  const saved = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  try {
    keys.forEach(key => Object.defineProperty(globalThis, key, { configurable: true, get() { throw new Error('Persistence forbidden') } }))
    await harness().signIn(credentials)
    await harness().signIn({ username: '管理员', password: 'x' })
  } finally {
    keys.forEach((key, index) => { if (saved[index]) Object.defineProperty(globalThis, key, saved[index]); else delete globalThis[key] })
  }
})
