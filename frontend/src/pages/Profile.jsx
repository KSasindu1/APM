import { useNavigate } from 'react-router-dom'
import { profileDetails } from '../services/profileView.js'
import './Profile.css'

export default function Profile({ session, onUnauthorized }) {
  const navigate = useNavigate()
  const profile = profileDetails(session)
  const signOut = () => {
    onUnauthorized(session)
    navigate('/login', { replace: true })
  }

  return (
    <main className="profile-page">
      <header className="profile-page__heading">
        <p className="profile-page__eyebrow">YOUR ACCOUNT</p>
        <h1>My profile</h1>
        <p>Your signed-in account details and workspace access.</p>
      </header>
      <section className="profile-card" aria-labelledby="profile-account-heading">
        <div className="profile-card__identity">
          <span className="profile-card__avatar" aria-hidden="true">{Array.from(profile.username)[0].toUpperCase()}</span>
          <div>
            <h2 id="profile-account-heading">{profile.username}</h2>
            <p>{profile.role === 'admin' ? 'Administrator' : 'Project user'}</p>
          </div>
        </div>
        <dl className="profile-card__details">
          <div><dt>Username</dt><dd>{profile.username}</dd></div>
          <div><dt>Role</dt><dd>{profile.role === 'admin' ? 'Administrator' : 'User'}</dd></div>
          <div><dt>Account type</dt><dd>{profile.accountType}</dd></div>
          {profile.userId && <div><dt>Account ID</dt><dd><code>{profile.userId}</code></dd></div>}
          <div><dt>Session expires</dt><dd><time dateTime={profile.expiresAt}>{new Date(profile.expiresAt).toLocaleString()}</time></dd></div>
        </dl>
        <div className="profile-card__access">
          <h3>Workspace access</h3>
          <p>{profile.role === 'admin'
            ? 'You can manage project knowledge and access planning, schedules, and monitoring for all projects.'
            : 'You can plan projects, view schedules, and monitor progress for your own projects.'}</p>
        </div>
        <p className="profile-card__note">These details are read-only. Signing out ends this browser session; refreshing the page requires signing in again.</p>
        <button type="button" className="profile-card__signout" onClick={signOut}>Sign out</button>
      </section>
    </main>
  )
}
