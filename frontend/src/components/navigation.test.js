import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { workspaceHome, workspaceLinks, workspaceSection } from './navigation.js'

const source = path => readFile(new URL(path, import.meta.url), 'utf8')

test('workspace sections include main and project pages, not landing, auth or unknown routes', () => {
  const sections = {
    '/dashboard': 'Project workspace',
    '/admin/dashboard': 'Administrator workspace',
    '/coordinator': 'Project coordinator',
    '/profile': 'Your profile',
    '/planning': 'Planning & scheduling',
    '/schedule': 'Project schedules',
    '/schedule/project-one': 'Project schedules',
    '/monitoring': 'Monitoring & reporting',
    '/monitoring/project-one': 'Monitoring & reporting',
  }
  for (const [path, subtitle] of Object.entries(sections)) {
    assert.equal(workspaceSection(path), subtitle)
    assert.equal(workspaceSection(`${path}/`), subtitle)
  }
  for (const path of ['/', '/login', '/signup', '/admin/login', '/admin/signup', '/unknown', '/schedule-other', '/monitoring/a/b']) {
    assert.equal(workspaceSection(path), null)
  }
})

test('top-bar links use the verified role and keep detail pages within their active section', () => {
  for (const role of ['user', 'admin']) {
    const links = workspaceLinks(role)
    assert.deepEqual(links.map(link => link.label), ['Home', 'Coordinator', 'Planning', 'Schedule', 'Monitoring', 'Profile'])
    assert.equal(links[0].to, role === 'admin' ? '/admin/dashboard' : '/dashboard')
    assert.equal(links[0].to, workspaceHome(role))
    assert.equal(links[1].to, '/coordinator')
    assert.equal(links[5].to, '/profile')
    assert.equal(links[5].end, true)
    assert.equal(new Set(links.map(link => link.to)).size, links.length)
    assert.ok(links[0].end && links[1].end && links[2].end)
    assert.equal(links[3].end, false)
    assert.equal(links[4].end, false)
  }
})

test('one sticky workspace bar replaces the blanket Back/Home row and Monitoring-specific header', async () => {
  const header = await source('./WorkspaceHeader.jsx')
  assert.match(header, /aria-label="Workspace navigation"/)
  assert.match(header, /<NavLink key=\{to\} to=\{to\} end=\{end\}/)
  assert.match(header, /if \(!subtitle\) return null/)
  assert.match(header, /aria-label="APM home"/)
  assert.match(header, /<Link to=\{workspaceHome\(role\)\}/)
  const css = await source('./WorkspaceHeader.css')
  assert.match(css, /position: sticky/)
  assert.match(css, /top: 0/)
  assert.match(css, /aria-current="page"/)
  assert.match(css, /@media print[\s\S]*display: none/)
  const app = await source('../App.jsx')
  assert.doesNotMatch(app, /PageNavigation|Back to home/)
  assert.match(app, /isAccountSessionValid\(accountSession\) \|\| isAdminSessionValid\(session\)/)
  assert.match(app, /<WorkspaceHeader role=\{session\?\.role \?\? accountSession\?\.role\}/)
  const monitoring = await source('../pages/Monitoring.jsx')
  assert.doesNotMatch(monitoring, /<header className="monitoring-header"/)
})

test('shared Monitoring hides workflow and technical details while preserving project access', async () => {
  const monitoring = await source('../pages/Monitoring.jsx')
  assert.doesNotMatch(monitoring, /aria-label="Monitoring workflow"|From inputs to a reviewable report/)
  assert.doesNotMatch(monitoring, /monitoring-api-status|monitoring-api-links|monitoring-capabilities/)
  assert.doesNotMatch(monitoring, /Verified Monitoring identity|Server-granted permissions|Public health JSON|publicApiLinks|MONITORING_API_URL/)
  assert.match(monitoring, /onClick=\{onRefresh\}>Refresh projects<\/button>/)
  assert.match(monitoring, /verifyAccountMonitoringSession\(session, \{ signal \}\)/)
  assert.match(monitoring, /createMonitoringApi\(\)\.health\(signal\)/)
  assert.match(monitoring, /const capabilities = health\.value\?\.capabilities/)
  assert.match(monitoring, /capabilities=\{capabilities\}/)
  assert.match(monitoring, /session\.permissions\.includes\('monitoring:generate'\)/)
  assert.match(monitoring, /admin \? 'All projects' : 'Owned projects'/)
  assert.match(monitoring, /report\.summary_method === 'llm_extractive' \? 'AI-assisted summary' : 'Monitoring summary'/)
  assert.match(monitoring, /Human review required\.<\/strong>/)
  const css = await source('../pages/Monitoring.css')
  assert.match(css, /\.monitoring-page \.monitoring-hero \{[^}]*grid-template-columns: minmax\(0, 1fr\);/)
  assert.doesNotMatch(css, /monitoring-hero aside|monitoring-api-status|monitoring-api-links|monitoring-capabilities/)
})

test('return actions are limited to account forms and contextual project details', async () => {
  const auth = await source('../pages/AccountAuth.jsx')
  assert.match(auth, /onClick=\{back\}>Back to home<\/button>/)
  const monitoring = await source('../pages/Monitoring.jsx')
  assert.match(monitoring, /routeProjectId && <nav[^>]*aria-label="Project navigation"[^>]*><Link to="\/monitoring">← Back to Monitoring projects/)
  const schedule = await source('../pages/Schedule.jsx')
  assert.match(schedule, /\{schedule && \([\s\S]*handleBackToProjects[\s\S]*Back to all projects/)
  const admin = await source('../admin/AdminDashboard.jsx')
  assert.doesNotMatch(admin, /Back to home|onBack/)
  const dashboard = await source('../pages/Dashboard.jsx')
  assert.doesNotMatch(dashboard, /Back to home|navigate\(-1\)/)
})

test('the home page has no separate admin login and legacy auth links redirect to common sign-in', async () => {
  const app = await source('../App.jsx')
  const landing = app.slice(app.indexOf('function LandingPage'), app.indexOf('function AccountLoginPage'))
  assert.doesNotMatch(landing, /Admin Login|to="\/admin\/login"/)
  for (const path of ['/admin/login', '/admin/signup']) assert.ok(app.includes(`<Route path="${path}" element={<Navigate to="/login" replace />} />`))
  assert.match(app, /navigate\(data\.role === "admin" \? "\/admin\/dashboard" : "\/dashboard"/)
})

test('admin dashboard keeps protected knowledge features without invitation or ownership panels', async () => {
  const admin = await source('../admin/AdminDashboard.jsx')
  assert.doesNotMatch(admin, /AdminInvitations|ProjectOwnership/)
  assert.match(admin, /Upload project document/)
  assert.match(admin, /async function deleteDocument/)
  assert.match(admin, /async function handleSearch/)
  const ownership = await source('../../../backend/app/api/planning.py')
  assert.match(ownership, /owner_user_id=actor\.owner_filter/)
})

test('dev Coordinator integration keeps local guards and authenticated Planning autofill', async () => {
  const app = await source('../App.jsx')
  assert.match(app, /import Coordinator from "\.\/pages\/Coordinator"/)
  assert.ok(app.includes('<Route path="/coordinator" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Coordinator} />} />'))
  const dashboard = await source('../pages/Dashboard.jsx')
  assert.match(dashboard, /to="\/coordinator"/)
  assert.match(dashboard, /Project Coordinator/)
  const planning = await source('../pages/Planning.jsx')
  assert.match(planning, /function Planning\(\{ session, onUnauthorized \}\)/)
  assert.match(planning, /const draft = location.state\?\.coordinatorDraft/)
  assert.match(planning, /useState\(\(\) => \(\{/)
  for (const field of ['project_id', 'project_name', 'objective', 'start_date', 'deadline']) assert.ok(planning.includes(`draft?.${field}`))
  for (const field of ['tasks', 'resources', 'risks']) assert.ok(planning.includes(`Array.isArray(draft?.${field}) ? draft.${field} : []`))
  assert.match(planning, /Inputs received from Coordinator/)
  assert.match(planning, /createSchedule\(form, \{ session \}\)/)
  assert.match(planning, /onUnauthorized\?\.\(session\)/)
  assert.match(planning, /planningErrorMessage\(err\)/)
})

test('Monitoring entry points retain signed-in guards and the selected schedule project', async () => {
  const app = await source('../App.jsx')
  for (const path of ['/monitoring', '/monitoring/:projectId']) {
    assert.ok(app.includes(`<Route path="${path}" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Monitoring} />} />`))
  }
  const dashboard = await source('../pages/Dashboard.jsx')
  assert.match(dashboard, /<Link to="\/monitoring"/)
  assert.match(dashboard, /your own projects/)
  const admin = await source('../admin/AdminDashboard.jsx')
  assert.match(admin, /<Link to="\/monitoring"/)
  assert.match(admin, /Monitor all projects/)
  const schedule = await source('../pages/Schedule.jsx')
  assert.ok(schedule.includes('to={`/monitoring/${encodeURIComponent(schedule.project_id)}`}'))
  assert.match(schedule, /Monitor progress/)
})
