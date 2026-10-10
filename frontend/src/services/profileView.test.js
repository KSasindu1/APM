import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
import { profileDetails } from './profileView.js'

const now = 1800000000000
const expiry = now / 1000 + 300
const userId = '1b5e7946-0f72-4a41-9c5e-4184c817790b'
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url')
const session = (role = 'user', configured = false, username = configured ? '管理员😀' : 'alice') => ({
  username, role, token_type: 'bearer', expires_at: expiry,
  ...(configured ? {} : { user_id: userId }),
  access_token: `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: username, role, exp: expiry, iat: now / 1000 - 10, iss: 'apm', aud: 'apm-accounts', token_use: configured ? 'legacy_admin' : 'account_access', ...(configured ? {} : { account_id: userId }) })}.c2lnbmF0dXJl`,
})
const source = path => readFile(new URL(path, import.meta.url), 'utf8')

for (const role of ['user', 'admin']) {
  test(`${role} profile projects managed-account fields without credentials`, () => {
    const input = { ...session(role), password: 'private-password', email: 'invented@example.test', api_key: 'private-key' }
    const result = profileDetails(input, { now })
    assert.deepEqual(result, { username: 'alice', role, userId, accountType: 'Registered account', expiresAt: new Date(expiry * 1000).toISOString() })
    assert.ok(Object.isFrozen(result))
    assert.equal(JSON.stringify(result).includes(input.access_token), false)
    for (const key of ['password', 'email', 'api_key', 'access_token', 'token_type']) assert.equal(Object.hasOwn(result, key), false)
    assert.equal(input.password, 'private-password')
  })
}

test('configured administrator retains Unicode identity without fabricating a UUID', () => {
  const result = profileDetails(session('admin', true), { now })
  assert.equal(result.username, '管理员😀')
  assert.equal(result.role, 'admin')
  assert.equal(result.userId, null)
  assert.equal(result.accountType, 'Configured administrator')
})

test('profile rejects missing, expired, inconsistent and foreign account identity', () => {
  for (const invalid of [null, {}, { ...session(), expires_at: now / 1000 - 1 }, { ...session(), username: 'other' }, { ...session(), role: 'admin' }, { ...session(), user_id: 'aa2db83e-3b6d-4998-b3c6-d605b4d9b30e' }, { ...session('admin'), user_id: 'bad-id' }, { ...session('admin', true), user_id: userId }]) {
    assert.throws(() => profileDetails(invalid, { now }))
  }
  assert.throws(() => profileDetails(session(), { now: expiry * 1000 }))
})

test('Home and auth routes choose the signed-in workspace; profile uses server verification gate', async () => {
  const app = await source('../App.jsx')
  assert.match(app, /const signedInHome = isAdminSessionValid\(session\)[\s\S]*workspaceHome\("admin"\)[\s\S]*isAccountSessionValid\(accountSession\) \? workspaceHome\("user"\) : null/)
  for (const path of ['/', '/login', '/signup']) {
    assert.ok(app.includes(`<Route path="${path}" element={signedInHome ? <Navigate to={signedInHome} replace /> : `))
  }
  assert.ok(app.includes('<Route path="/profile" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Profile} />} />'))
  const guard = app.slice(app.indexOf('function ProjectWorkspacePage'), app.indexOf('function useSessionExpiry'))
  assert.match(guard, /client\.verifySession\(session, \{ signal: controller\.signal \}\)/)
  assert.match(guard, /verification\?\.session === session && verification\.attempt === attempt/)
  assert.match(guard, /if \(!current \|\| !verification\.verified\)/)
  assert.match(guard, /if \(!validSession\) return <Navigate to="\/login" replace \/>/)
  assert.ok(guard.indexOf('if (!current || !verification.verified)') < guard.indexOf('return <Page session={session}'))
  assert.match(app, /current === expectedSession \? null : current/)
})

test('profile displays only account details, has no editor and signs out the originating session', async () => {
  const page = await source('../pages/Profile.jsx')
  assert.match(page, /profileDetails\(session\)/)
  assert.match(page, /onUnauthorized\(session\)/)
  assert.match(page, /navigate\('\/login', \{ replace: true \}\)/)
  assert.match(page, /profile\.userId &&/)
  assert.match(page, /<time dateTime=\{profile\.expiresAt\}/)
  assert.match(page, /Array\.from\(profile\.username\)/)
  assert.doesNotMatch(page, /access_token|api_key|password|<input|<form|localStorage|sessionStorage|navigate\(-1\)|Back to home/)
})
