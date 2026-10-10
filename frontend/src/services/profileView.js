import { validateAccountSession } from './accountApi.js'
import { validateAdminSession } from './adminApi.js'

// Display projection only; the route must verify the session on the server first.
export function profileDetails(session, { now = Date.now() } = {}) {
  let current
  let userId = null
  if (session?.role === 'admin') {
    current = validateAdminSession(session, { now })
    if (session.user_id !== undefined) {
      userId = validateAccountSession(session, { now }).user_id
    }
  } else {
    current = validateAccountSession(session, { now })
    userId = current.user_id
  }
  return Object.freeze({
    username: current.username,
    role: current.role,
    userId,
    accountType: userId ? 'Registered account' : 'Configured administrator',
    expiresAt: new Date(current.expires_at * 1000).toISOString(),
  })
}
