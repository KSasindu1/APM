import { isAccountSessionValid } from './accountApi.js'
import { isAdminSessionValid } from './adminApi.js'
import { createMonitoringApi, MonitoringApiError, validateMonitoringSession } from './monitoringApi.js'

const sessionKeys = new WeakMap()
let nextSessionKey = 0

// Object identity, not a token in a DOM key: even replacing a session with the
// same bearer must discard the previous verification and all private inputs.
export function monitoringAccountKey(session) {
  if (!session || typeof session !== 'object') return 'signed-out'
  if (!sessionKeys.has(session)) sessionKeys.set(session, ++nextSessionKey)
  return sessionKeys.get(session)
}

export async function verifyAccountMonitoringSession(session, { signal, api, now = Date.now } = {}) {
  const check = () => {
    signal?.throwIfAborted()
    const valid = session?.user_id !== undefined || session?.role !== 'admin'
      ? isAccountSessionValid(session, now())
      : isAdminSessionValid(session, now())
    if (!valid) throw new MonitoringApiError('Your signed-in session is invalid or has expired. Sign in again.', 401)
  }
  check()
  // Route-level account/admin verification must precede this protected request.
  // Decoded claims alone never grant Monitoring access.
  const response = await (api || createMonitoringApi({ token: session.access_token })).session(signal)
  check()
  const data = validateMonitoringSession(response)
  if (session.user_id !== undefined && data.subject !== session.user_id) {
    throw new MonitoringApiError('Monitoring returned a different account identity. No project access was granted.')
  }
  // Bootstrap admins may use a stable ASCII adapter subject (not the username).
  // It is accepted only from the session endpoint verified with this bearer.
  return data
}
