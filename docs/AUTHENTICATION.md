
# Authentication and authorization

Authentication verifies the caller's identity. Authorization determines which operations and projects that identity may access. The app supports persistent user accounts and the existing configured administrator through one sign-in page. The UI assumes one administrator and provides no administrator invitation or registration controls; protected legacy managed-administrator APIs remain available for compatibility. Shared Monitoring uses the same verified login and stored project ownership as Planning. Standalone Monitoring retains its separate scoped-token contract; public registration never grants access to another user's projects.

## Current access boundaries

| Operation | Authorization |
| --- | --- |
| `POST /api/accounts/signup` | Public registration; server assigns `user`; no role parameter |
| `POST /api/accounts/login` | Registered account credentials; server returns the stored role |
| `GET /api/accounts/session` | Account JWT plus fresh account ID, username, active state, and role checks |
| `POST /api/accounts/admin-invitations` | Verified configured or managed administrator |
| `POST /api/accounts/admin-signup` | Valid, username-bound, unexpired, unused administrator invitation |
| `POST /api/auth/login` | Configured bootstrap administrator or registered administrator credentials |
| `GET /api/auth/session` | Verified configured or managed administrator, role, and expiry |
| `POST /api/coordinator/interpret` | Verified account or administrator Bearer; service keys alone are rejected |
| `POST /api/coordinator/request` | Existing backend-only Planning service-key contract; browser bearers do not grant service access |
| Planning project list/read/update/delete | Verified owner account; administrators or trusted Planning services may access all |
| Planning project creation | Verified creator becomes owner; bootstrap-admin/service creations remain unassigned |
| `PUT /api/planning/schedule/{project_id}/owner` | Verified administrator; explicit assignment to an active regular account |
| `POST /api/planning/message` | Configured backend-only Planning service key; browser bearers rejected |
| Risk PDF upload, document list, and chunk inspection | Administrator JWT |
| `POST /api/risk/knowledge/search` | Administrator JWT; knowledge retrieval follows project filters |
| Shared Monitoring baseline/report/observation reads | Verified owner account or administrator; fresh existing-project check plus `monitoring:read` |
| Shared Monitoring observation/report generation | Verified owner account or administrator; fresh existing-project check plus `monitoring:generate` |
| Standalone Monitoring operations | Separately configured scoped Monitoring bearer and required project/operation permissions |

An account/admin JWT grants shared Monitoring access only after backend identity and stored ownership checks: users monitor their own projects, while administrators may monitor all existing projects. Standalone Monitoring tokens do not grant shared application or administrator rights. Entering a project ID or importing a Planning message is not evidence of ownership. Administrators retain the existing global knowledge-management role; ordinary users' memberships are not inferred from that role. Inviting another administrator grants that global administrator role, including invitation issuance—not just access to one selected project.

## One-time account database setup

1. Back up the database as appropriate. Open **Supabase → SQL Editor** in the existing project using your trusted migration account.
2. Run `backend/sql/auth_accounts.sql` once. It adds `public.auth_accounts`, `public.auth_admin_invitations`, and three restricted account/invitation RPCs. It does not change the Planning or Monitoring tables.
3. Keep `SUPABASE_URL` and `SUPABASE_SECRET_KEY` on the backend only. The key must support the trusted `service_role`; a browser/anonymous key cannot access these tables or functions. The application does not create these objects automatically and has no in-memory or local account fallback.
4. Ensure the existing JWT signing configuration is valid, then restart the backend. Do not rotate a working signing key unnecessarily.
5. Start/restart Vite and open `/signup` to create an ordinary user account.

The script is deliberately a **one-time migration**, not a reset command. If these auth tables already exist, stop and use a reviewed in-place migration; do not drop accounts or rerun `CREATE TABLE` statements blindly. Database privileges and real concurrent behavior must be verified in your deployment before wider release.

Accounts are application-managed rows in `public.auth_accounts`, **not Supabase Auth `auth.users` identities**. Passwords are salted PBKDF2-SHA256 hashes, never plaintext. The UUID returned by the server and displayed in the user dashboard is the account's user ID. There is no email-address collection or email-verification claim in this username/password flow.

### User flow

1. Choose **Sign up** on Home or open `/signup`.
2. Enter a username and password twice. New usernames are canonical lowercase ASCII, 3–50 characters, start with a letter/digit, and may contain dots, underscores, or hyphens. The bootstrap administrator's name is reserved.
3. Use a nonblank password of 12–256 Unicode characters. Spaces are preserved exactly; they are not trimmed. Registration creates a `user` account and returns no session token.
4. Sign in at `/login`. The frontend verifies `/api/accounts/session` before opening the user dashboard; its dashboard boundary verifies again before showing signed-in identity.
5. Open **Monitor Projects** on the dashboard or **Monitor progress** on a schedule. Monitoring verifies the same session and lists owned projects; no extra token is entered. Record actual progress explicitly and review coverage before generating a report. Saving reports/observations requires their existing backend storage setup.
6. Sign out clears the in-memory session. Browser refresh also requires sign-in again. Planning, Schedule, and shared Monitoring enforce stored project ownership on the backend. Standalone Monitoring remains separately scoped.

Public signup rejects extra request fields such as `role`, `user_id`, and invitation metadata. Usernames are visible account identifiers, not private email addresses. Duplicate/unavailable-name feedback uses a generic 409; username availability can therefore be discovered, subject to request limiting.

### Administrator flow

1. Choose **Sign in** on Home or open `/login`. Enter the existing configured administrator credentials, preserving username case and password spaces.
2. The common form tries account authentication for a compatible username, then configured-administrator authentication only after an account-login 401 or 503. Unicode/non-account usernames go directly to administrator authentication. Other failures, including session-verification failures, never trigger fallback.
3. The selected server session endpoint verifies the session before the server-assigned role routes administrators to `/admin/dashboard` and ordinary users to `/dashboard`. The dashboard boundary verifies again before rendering private content.
4. `/admin/login` and `/admin/signup` are compatibility redirects to `/login`. There is no separate administrator button on Home and no invitation or project-ownership panel in the admin dashboard.

This is a UI change, not a database constraint limiting the number of administrators. Existing accounts, invitation records, and protected invitation/signup APIs are preserved; no account is deleted or demoted.

User creation, invitation issuance, and invitation redemption share a database transaction-scoped username lock. A valid outstanding invitation reserves its username against public signup. Redemption checks username, digest, expiry, and unused state atomically with inserting a **new** administrator account; an existing public account is never promoted. Failed account insertion rolls back invitation consumption. Expired reservations do not permanently block ordinary registration.

Invitation creation is admin-only on the backend, regardless of whether someone modifies browser HTML. Issuer deactivation does not automatically revoke their previously issued invitations; use a trusted operational revocation procedure if needed. Cancellation only stops local waiting: a submitted registration or invitation operation may already have completed on the server.

## Project ownership setup and flow

Apply `backend/sql/project_ownership.sql` once through a trusted Supabase migration connection, after the existing `project_schedules` schema and `auth_accounts.sql`. This adds a nullable `owner_user_id` UUID referencing app-managed accounts, an index, and restricted table privileges. It does not delete schedules, modify their JSON, or automatically assign old projects. If the column already exists, use a reviewed in-place migration rather than rerunning or resetting the database.

- A managed creator's UUID is taken from the server-verified token, not a request field. New browser-created project IDs use INSERT, so another user cannot overwrite or claim an existing project through creation.
- Normal users' lists and direct reads are filtered in the database by owner UUID. Foreign, unassigned, and missing project IDs return the same 404. Updates/deletes apply that condition again at the actual write, so reassignment during generation cannot bypass access checks.
- Administrators retain access to all schedules. The dashboard no longer exposes project-ownership controls. If an old or service-created project needs assignment, a trusted operator can use the preserved administrator-only `PUT /api/planning/schedule/{project_id}/owner` API with an active regular account's canonical username. Reassignment changes future normal-user access; reload already-open lists to discard previously authorized cached results.
- Existing projects, bootstrap-admin-created projects, and service-created projects without an owner remain unassigned and hidden from ordinary users until explicitly assigned. Deleting an account sets its project ownership to NULL; it does not delete its projects.
- The frontend's `/planning`, `/schedule`, and `/monitoring` routes verify sessions before mounting. Browser project requests send the account/admin bearer and never send the Planning service key. Admin assignment is backend-enforced even if browser HTML or payloads are modified.
- Project reads use a 30-second client timeout; schedule generation/recalculation use a separate three-minute budget. A timed-out creation performs one owner-scoped lookup for a completed result, never another POST. If completion remains unknown, check the project list before resubmitting. An ambiguous recalculation never reports the old schedule as the successful update. Cancellation prevents stale UI effects but does not roll back server work.

### Agent/service access

Planning's previous key-optional development bypass is removed: it would otherwise expose all users' projects. For trusted backend agent calls, configure a private `PLANNING_API_KEY` on the backend and send it as `X-API-Key`. Shared browser Monitoring now reads Planning through a request-owned, owner-filtered repository adapter and does not require this service key. A separate Monitoring deployment must use its existing Planning-provider credential setting. Keep service credentials out of `VITE_*` settings and browsers. If an old key was browser-visible, coordinate replacement with the team before relying on it as a private credential.

A supplied Authorization header always takes precedence and is verified; a bad bearer never falls back to a valid service key. `/api/planning/message` accepts only service authentication. Trusted services retain the existing owner-preserving schedule upsert contract and global schedule access; browser calls use owner-scoped operations. A missing service-key configuration makes unauthenticated service access unavailable, not public. The health endpoint remains public.

Planning/Schedule and shared Monitoring use the same single-owner model. This does not add multi-user project memberships, change standalone Monitoring grants, change Risk source-to-source authorization, or alter scheduling/risk algorithms. Keep the remaining source API boundaries below in mind before deployment.

## Operator setup

Keep configuration in the existing private `backend/.env` or trusted process environment. Preserve the team's Supabase, Groq, Planning, and Monitoring settings. Never commit secrets, put secrets in `VITE_*` variables, or send them to an agent/shared transcript. Public API roots and audience names are non-secret configuration; account creation still does not approve project membership.

Admin configuration:

| Variable | Contract |
| --- | --- |
| `ADMIN_USERNAME` | Actual configured administrator name, 1–100 Unicode code points; no edge whitespace/BOM, C0 controls, DEL, or invalid UTF-8. Ordinary configuration whitespace is trimmed. |
| `ADMIN_PASSWORD_HASH` | Preferred salted PBKDF2-SHA256 hash produced by the helper below. An explicitly configured empty or malformed hash fails closed; it never falls back to plaintext. |
| `ADMIN_PASSWORD` | Legacy compatibility only when the hash setting is absent. Exact password whitespace is significant; do not use a blank/whitespace-only password. |
| `JWT_SECRET_KEY` | Private cryptographically random signing key; at least 32 UTF-8 bytes for HS256, 48 for HS384, or 64 for HS512. Length alone does not establish entropy. |
| `JWT_ALGORITHM` | `HS256` by default; only HS256, HS384, and HS512 supported. Never `none`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Integer 1–1,440; default 60. Prefer short lifetimes. |
| `JWT_ISSUER` | Nonblank trusted issuer; default `apm`. |
| `JWT_AUDIENCE` | Nonblank trusted bootstrap-admin audience; default `apm-admin`. |
| `ACCOUNT_JWT_AUDIENCE` | Nonblank managed-account audience; default `apm-accounts`; must differ from `JWT_AUDIENCE`. |

The default frontend works with `ACCOUNT_JWT_AUDIENCE=apm-accounts`. If you deliberately customize that backend value, set the public frontend `VITE_ACCOUNT_JWT_AUDIENCE` to the same audience and restart/rebuild Vite. Never put signing keys, database keys, passwords, or invitation codes in a Vite setting.

### Generate a password hash privately

In your own private PowerShell terminal, from `backend/`, run:

```powershell
.\venv\Scripts\python.exe -B -c "from getpass import getpass; from app.core.security import hash_admin_password; print(hash_admin_password(getpass('New admin password: ')))"
```

The password is entered without echo; only its salted hash is printed. Capture the hash privately as `ADMIN_PASSWORD_HASH`, preserving the entire `pbkdf2_sha256$iterations$salt$digest` string. In a dotenv file, enclose that hash in single quotes so its dollar signs remain literal. Do not run this command through a shared agent terminal, paste the output into chat, or put the password itself in command arguments. The helper uses a random 32-byte salt and at least 600,000 PBKDF2-SHA256 iterations; verification bounds configured iterations to at most 2,000,000.

Once the hash is configured, remove the obsolete plaintext `ADMIN_PASSWORD` setting. Keep a private operational recovery procedure; the application neither resets passwords nor creates default accounts.

For default HS256, generate signing material separately in your private terminal:

```powershell
.\venv\Scripts\python.exe -B -c "import secrets; print(secrets.token_urlsafe(32))"
```

Capture it privately as `JWT_SECRET_KEY`; do not share or reuse it as a Monitoring token. Do not overwrite a working key casually: rotation invalidates existing JWTs.

Restart the shared backend after configuration changes. Missing/invalid admin configuration returns a sanitized 503 rather than permitting empty credentials or unsafe token signing. This does not disable independent Monitoring access or supply missing database configuration.

## Run and sign in

Run one shared backend from `backend/`:

```powershell
.\venv\Scripts\python.exe -B -m uvicorn app.main:app --reload --port 8000
```

Run the existing frontend in another terminal, from `frontend/`:

```powershell
npm run dev
```

Open `/login` at Vite's actual origin, normally `http://localhost:5173/login`. Both users and the configured administrator use this form. Enter the username and actual password—not the stored hash or signing key.

The account/admin clients default to the current browser origin. Vite proxies `/api/auth`, `/api/accounts`, `/api/planning`, `/api/risk`, and the existing `/api/monitoring` to `http://127.0.0.1:8000`. An explicit `VITE_API_BASE_URL` overrides this route and must be a server root without credentials, paths, queries, or fragments. Admin credentials/bearers are sent only to HTTPS roots outside loopback. Deployed frontend builds need an appropriate reverse proxy or an explicit HTTPS API URL; the development proxy is not shipped in the build.

### Browser flow

1. The common form sends only `username` and `password` to `POST /api/accounts/login` or, under the administrator fallback rules above, `POST /api/auth/login`. Only the account attempt lowercases compatible usernames; configured-administrator input retains case and Unicode, and passwords are never trimmed.
2. A successful response has `access_token`, `token_type="bearer"`, `username`, a server-assigned `role`, and integer Unix-second `expires_at`. Managed accounts also include `user_id`.
3. Before accepting the session, the browser calls `GET /api/accounts/session` for an account response or `GET /api/auth/session` for an administrator-auth response. These return matching identity, role, and expiry metadata without a token. Protected route boundaries verify again before rendering private content.
4. Every protected operation validates JWT signature, trusted algorithm, issuer/audience, required claims, token purpose, timestamps, and role on the backend. Bootstrap tokens additionally match the configured administrator's subject. Managed tokens additionally match a fresh database account's UUID, canonical username, active state, and stored role; changing/deactivating/deleting that account invalidates its previous authorization.
5. The token stays in React memory, not local/session storage or cookies. Expiry and active-request 401 responses clear the session; refresh requires signing in again. A 403 is an authorization denial, not automatically a bad password or expired session.
6. Sign out clears private UI state and cancels local pending work. It does **not** revoke an already issued JWT on the server. A cancelled upload may already have completed; inspect the project before retrying it.

Signed-in workspace pages share one sticky top bar with Home, Coordinator, Planning, Schedule, Monitoring, and Profile links, highlighting the current section. Home and the APM logo open `/dashboard` for users or `/admin/dashboard` for administrators. Signed-in visits to `/`, `/login`, or `/signup` redirect to that role-appropriate home, rather than rendering the public landing page or account forms. Signed-out visitors may use the public landing, login, and signup pages; direct visits to workspace pages, including Coordinator and Profile, redirect to login before mounting private content. Main dashboards and section pages have no redundant Back/Home row. Login and signup retain one **Back to home** action. Schedule details return to the schedule list; Monitoring project details return to the Monitoring chooser, including when a detail URL was opened directly. These are explicit in-app destinations, not browser-history guesses. Navigation unmounts page-specific work; returning home does not sign out, while sign-out still clears the session and private state.

The read-only `/profile` page is behind the same server-verified route boundary as project pages. It displays username, role, account type, session expiry, and a UUID only for a validated managed account. Configured administrators have no fabricated account ID. It does not display tokens, passwords, service keys, or invented email/name fields, and provides no account-editing controls. Sign out clears only the originating session and returns to the common login form. No browser token persistence or profile-edit API was added.

Coordinator interpretation sends only the signed-in Bearer, supports cancellation, and clears only the originating session on an active 401. Browser service-key configuration is no longer used. The service-only Coordinator request endpoint remains separate from the browser's interpretation → Planning draft workflow.

Browser JWT decoding only checks response consistency and schedules expiry; it does not verify signatures. The protected backend session endpoint and operation dependencies remain authoritative. Late login, verification, project/document/query, and upload responses cannot restore an old session or cross into a newer UI selection. Malformed display fields and foreign response metadata are rejected before state updates.

## Shared Monitoring access

- Users may read baselines, record actuals, generate reports, and review saved reports/observations for their owned, existing projects. Administrators have the same operations for all existing projects, including unassigned ones. Both `monitoring:read` and `monitoring:generate` are server-granted for those projects.
- `/api/monitoring/session` returns verified `subject`, project IDs, and permissions without returning a bearer. Managed subjects are account UUIDs; the configured administrator uses the stable ASCII adapter subject `bootstrap-admin`, preserving Unicode username support without relaxing the core identifier contract.
- Monitoring identifiers remain `[A-Za-z0-9][A-Za-z0-9_.-]{0,99}`. Accessible legacy project IDs outside that contract are omitted from the chooser and disclosed through an optional `unsupported_project_count`; they do not block compatible projects or change any schedule. Task IDs must also satisfy the existing Monitoring contract.
- Every project operation checks fresh account state and ownership before source or Monitoring-storage access. Imported Planning messages, receipt IDs, and report IDs cannot grant access. Foreign, unassigned, and missing projects return the same 404 to ordinary users.
- Slow operations recheck authentication and ownership before persistence and response release. These are not cross-database transactions: a write already completed during reassignment cannot be undone, though its response is withheld after failed revalidation.
- Refreshing project access or changing routes/session clears prior Monitoring inputs/results and cancels local work. A 401 clears the originating login; 403/404 clear private Monitoring content without treating every denial as sign-out. Backend work may already have completed when a browser request is cancelled.
- Shared Monitoring rejects standalone scoped tokens and Planning service keys as browser identity. Standalone factories retain their original explicit token/permission contract. Browser bearers are never forwarded to Risk or Supabase.

## Security behavior and compatibility

- Auth responses and errors use `Cache-Control: no-store` and `Pragma: no-cache`. Validation failures do not echo password/token input values.
- Login bodies are limited to 4 KiB. There are at most five attempts per actual ASGI peer IP per 60-second window, and at most 1,024 active peer entries. Forwarded headers are not accepted as identity by the limiter.
- The limiter is **process-local**: workers do not share budgets, restarts reset them, and clients behind a proxy may share one observed address. Production needs shared enforcement and deliberately configured proxy/IP trust; configure server/proxy forwarding trust separately.
- Existing JWTs without required issuer/audience/purpose/date claims are rejected. Sign in again after deploying this change. Password whitespace is no longer stripped; legacy edge-space passwords may need operator correction.
- PostgreSQL knowledge retrieval now skips tables without a recognized physical project-binding column. Text/JSON mentions of a project are not accepted as binding; previously unbound results disappear rather than being falsely labelled as the requested project's data. Review legitimate sources and their real schema explicitly.
- No new package dependency, membership synchronization, refresh-token flow, or per-token revocation store was introduced. Accounts and restricted invitations use the existing Supabase connection. Managed account tokens use `account_access` purpose and a distinct audience; they cannot impersonate bootstrap-admin tokens.

## Remaining deployment decisions — do not treat all APIs as secured

These are existing architecture boundaries, not solved by hardening the admin login:

- Risk `/analyse`, `/analyse-plan`, and `/agent/plan` remain public under their current source-to-source contracts. They can retrieve private project knowledge and invoke paid inference. Before wider deployment, approve service authentication, project grants/delegation, and the policy for manual analysis. A sender label, ASGI transport, or loopback origin is not authentication. Do not blindly forward browser credentials or apply an admin-only gate that breaks Monitoring/Planning calls.
- Planning enforces single-owner account access for browser operations and separately permits global access to authenticated administrators/trusted services. This is not a multi-member project model. The backend service key is privileged; do not expose it to browsers or infer service trust from loopback alone.
- PostgreSQL discovery still examines accessible tables with recognized project columns. Production should approve source tables/privileges rather than treating every readable table as Planning knowledge.
- Shared Monitoring is integrated with account authentication and single-owner Planning access. Standalone token mappings remain operationally maintained and are not synchronized with accounts. Shared project access is not a multi-member project model or authorization for the still-public Risk source APIs.
- JWT logout/password changes alone do not revoke existing signed tokens. Use short expiry, signing-key rotation where appropriate, or introduce a deliberate revocation/session policy.

Until these contracts are agreed and secured, keep development loopback-only or behind a trusted access-controlled boundary. Do not expose the application's source APIs to the public internet simply because the admin login is hardened. Use HTTPS for deployment and restrict database/service credentials to the backend.

## Offline validation

From `backend/`, using the existing isolated environment:

```powershell
.\venv\Scripts\python.exe -B -m pytest tests/test_accounts.py tests/test_auth.py tests/test_project_authorization.py tests/test_planning_ownership_api.py tests/test_planning_ownership_repository.py tests/test_monitoring_account_access.py -p no:cacheprovider
```

These tests use synthetic settings, guarded dotenv readers, stubbed models, and fake databases; they neither verify live credentials nor contact real services.

From `frontend/`:

```powershell
node --test src/services/accountApi.test.js src/services/adminApi.test.js src/services/signInApi.test.js src/services/planningApi.test.js src/services/monitoringApi.test.js src/services/monitoringView.test.js src/services/accountMonitoring.test.js src/services/coordinatorApi.test.js src/services/profileView.test.js src/components/navigation.test.js
node --input-type=module -e "import { build } from 'vite'; await build({ envDir: '.navigation-validation.local/empty-env' });"
```

Client tests are offline. For the environment-isolated build above, create an empty `frontend/.navigation-validation.local/empty-env` directory first and remove it afterward; the explicit `envDir` avoids reading application environment files. Mocked browser validation is separate from real authentication, database readiness, or cross-browser verification. Backend reservation/ownership/race tests use fake storage plus static migration checks; they do not execute the SQL against a live database.

If Windows Application Control blocks an installed DLL such as `orjson`, the affected environment may fail during FastAPI import before tests or startup. That is an environment/security-policy issue, not an invalid-password response. Resolve it through an approved dependency/environment process; do not disable security controls or treat a successful test run in another interpreter as proof that the blocked virtual environment works.
