import { Link, NavLink, useLocation } from 'react-router-dom'
import { workspaceHome, workspaceLinks, workspaceSection } from './navigation.js'
import './WorkspaceHeader.css'

export default function WorkspaceHeader({ role }) {
  const { pathname } = useLocation()
  const subtitle = workspaceSection(pathname)
  if (!subtitle) return null

  return (
    <header className="workspace-header">
      <div className="workspace-header__inner">
        <Link to={workspaceHome(role)} className="workspace-header__brand" aria-label="APM home">
          <span className="workspace-header__logo" aria-hidden="true">AP</span>
          <span className="workspace-header__identity">
            <strong>APM</strong>
            <small>{subtitle}</small>
          </span>
        </Link>
        <nav className="workspace-header__links" aria-label="Workspace navigation">
          {workspaceLinks(role).map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className="workspace-header__link">
              {label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  )
}
