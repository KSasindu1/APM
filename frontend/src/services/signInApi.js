import { AccountApiError, accountApi, validateAccountSession } from './accountApi.js'
import { AdminApiError, adminApi, validateAdminSession } from './adminApi.js'

const messages = {
  input: 'Use a username of 1–100 Unicode characters and a password of 1–256 Unicode characters. Password spaces are kept exactly.',
  configuration: 'Sign-in is not configured safely. Ask the administrator to check the API configuration.',
  response: 'The server returned an invalid or mismatched sign-in response. The response was not used.',
  expired: 'Your session has expired. Please sign in again.',
  network: 'Unable to reach the sign-in service. Check your connection and try again.',
  timeout: 'The sign-in request timed out. Please try again.',
}
const httpMessages = {
  400: 'Check the username and password and try again.',
  401: 'Invalid username or password.',
  403: 'The server denied sign-in. Contact the administrator if this persists.',
  422: 'Check the username and password and try again.',
  429: 'Too many requests. Please wait before trying again.',
  503: 'The sign-in service is unavailable. Please try again later.',
}

export class SignInError extends Error {
  constructor(kind, status = 0) {
    super(kind === 'http' ? httpMessages[status] || 'Sign-in failed. Please try again.' : messages[kind] || messages.response)
    this.name = 'SignInError'
    this.kind = kind
    this.status = status
  }
}

export function signInErrorMessage(error) {
  // Never display arbitrary exception messages or server error bodies.
  if (error instanceof SignInError || error instanceof AccountApiError || error instanceof AdminApiError) {
    return new SignInError(error.kind, error.status).message
  }
  return messages.response
}

const cancelled = () => new DOMException('Sign-in cancelled.', 'AbortError')
const checkCancellation = signal => { if (signal.aborted) throw cancelled() }
const requireResponse = condition => { if (!condition) throw new SignInError('response') }
const canonicalizable = username => /^[A-Za-z0-9][A-Za-z0-9_.-]{2,49}$/.test(username)

function credentials(input) {
  if (typeof input?.username !== 'string' || typeof input?.password !== 'string') throw new SignInError('input')
  const username = input.username.trim()
  const characters = Array.from(username)
  const passwordLength = Array.from(input.password).length
  if (!characters.length || characters.length > 100 || passwordLength < 1 || passwordLength > 256 || characters.some(character => {
    const point = character.codePointAt(0)
    return point < 32 || point === 127 || point >= 0xd800 && point <= 0xdfff
  })) throw new SignInError('input')
  return { username, password: input.password }
}

// Inject clients with the existing login/verifySession contracts for offline tests.
// They retain responsibility for transport configuration, timeouts and validation.
export function createSignInApi({ accountClient = accountApi, adminClient = adminApi, now = Date.now } = {}) {
  if (![accountClient, adminClient].every(client => typeof client?.login === 'function' && typeof client?.verifySession === 'function') || typeof now !== 'function') throw new SignInError('configuration')

  async function verifiedSession(client, candidate, signal, account, expectedUsername) {
    checkCancellation(signal)
    const validate = account ? validateAccountSession : validateAdminSession
    const session = validate(candidate, { now: now(), expectedUsername })
    let verified
    try { verified = await client.verifySession(session, { signal }) } catch (error) {
      // A verification denial is never a reason to try a second login endpoint.
      if ((error instanceof AccountApiError || error instanceof AdminApiError) && error.kind === 'http' && error.status === 401) throw new SignInError('expired', 401)
      throw error
    }
    checkCancellation(signal)
    requireResponse(verified && verified.username === session.username && verified.role === session.role && verified.expires_at === session.expires_at && (!account || verified.user_id === session.user_id))
    const current = validate(session, { now: now() })
    return Object.freeze({ ...current, role: verified.role })
  }

  return {
    async signIn(input, { signal = new AbortController().signal } = {}) {
      let abort
      const interruption = new Promise((_, reject) => {
        abort = () => reject(cancelled())
        signal.addEventListener('abort', abort, { once: true })
      })
      const work = async () => {
        checkCancellation(signal)
        const original = credentials(input)
        if (canonicalizable(original.username)) {
          let candidate
          let fallback = false
          try {
            candidate = await accountClient.login({ username: original.username.toLowerCase(), password: original.password }, { signal })
          } catch (error) {
            checkCancellation(signal)
            if (!(error instanceof AccountApiError) || error.kind !== 'http' || ![401, 503].includes(error.status)) throw error
            fallback = true
          }
          checkCancellation(signal)
          // Keep verification outside the fallback catch, including bad login output.
          if (!fallback) return verifiedSession(accountClient, candidate, signal, true, original.username.toLowerCase())
        }
        checkCancellation(signal)
        const candidate = await adminClient.login(original, { signal })
        checkCancellation(signal)
        return verifiedSession(adminClient, candidate, signal, false, original.username)
      }
      try { return await Promise.race([interruption, work()]) } catch (error) {
        if (signal.aborted || error?.name === 'AbortError') throw cancelled()
        if (error instanceof SignInError || error instanceof AccountApiError || error instanceof AdminApiError) throw new SignInError(error.kind, error.status)
        throw new SignInError('response')
      } finally {
        signal.removeEventListener('abort', abort)
      }
    },
  }
}

export const signIn = (input, options) => createSignInApi().signIn(input, options)
