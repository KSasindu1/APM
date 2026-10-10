const sections = [
  { pattern: /^\/dashboard\/?$/, subtitle: 'Project workspace' },
  { pattern: /^\/admin\/dashboard\/?$/, subtitle: 'Administrator workspace' },
  { pattern: /^\/coordinator\/?$/, subtitle: 'Project coordinator' },
  { pattern: /^\/profile\/?$/, subtitle: 'Your profile' },
  { pattern: /^\/planning\/?$/, subtitle: 'Planning & scheduling' },
  { pattern: /^\/schedule(?:\/[^/]+)?\/?$/, subtitle: 'Project schedules' },
  { pattern: /^\/monitoring(?:\/[^/]+)?\/?$/, subtitle: 'Monitoring & reporting' },
]

export function workspaceSection(pathname) {
  return sections.find(section => section.pattern.test(pathname))?.subtitle ?? null
}

export function workspaceHome(role) {
  return role === 'admin' ? '/admin/dashboard' : '/dashboard'
}

export function workspaceLinks(role) {
  return [
    { to: workspaceHome(role), label: 'Home', end: true },
    { to: '/coordinator', label: 'Coordinator', end: true },
    { to: '/planning', label: 'Planning', end: true },
    { to: '/schedule', label: 'Schedule', end: false },
    { to: '/monitoring', label: 'Monitoring', end: false },
    { to: '/profile', label: 'Profile', end: true },
  ]
}
