import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { accountApi, accountErrorMessage } from "../services/accountApi.js";
import { signIn, signInErrorMessage } from "../services/signInApi.js";
import "./AccountAuth.css";

const usernameHint = "3–50 lowercase ASCII characters, starting with a letter or digit. After the first character, dots, underscores and hyphens are also allowed. Uppercase letters are converted to lowercase.";
const loginUsernameHint = "Enter your username as configured. Leading and trailing whitespace is ignored; case and Unicode are preserved for administrator sign-in. 1–100 Unicode characters.";
const passwordHint = "12–256 well-formed Unicode characters, not only whitespace. Spaces are part of your password and are kept exactly.";
const wellFormedUnicode = value => Array.from(value).every(character => {
  const point = character.codePointAt(0);
  return point < 0xd800 || point > 0xdfff;
});

function AccountForm({ mode, onSuccess, onBack, registrationNotice }) {
  const signup = mode === "signup";
  const prefix = `account-${mode}`;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [completed, setCompleted] = useState(false);
  const form = useRef(null);
  const request = useRef({ sequence: 0, controller: null, mounted: false });

  useEffect(() => {
    const current = request.current;
    const element = form.current;
    current.mounted = true;
    return () => {
      current.mounted = false;
      current.sequence += 1;
      current.controller?.abort();
      current.controller = null;
      element?.querySelectorAll('input[type="password"]').forEach(input => { input.value = ""; });
    };
  }, []);

  function clearSecrets() {
    setPassword("");
    setConfirmation("");

  }

  function leave() {
    request.current.sequence += 1;
    request.current.controller?.abort();
    request.current.controller = null;
    clearSecrets();
    setUsername("");
    setError("");
    setLoading(false);
  }

  async function back() {
    leave();
    try { await onBack?.(); } catch { /* Parent navigation must not leak an unhandled rejection. */ }
  }

  function validateField(event) {
    const input = event.currentTarget;
    let message = "";
    if (input.name === "username" && !signup) {
      const characters = Array.from(input.value.trim());
      if (!characters.length || characters.length > 100) message = "Use 1–100 Unicode characters for your username.";
      if (characters.some(character => character.codePointAt(0) < 32 || character.codePointAt(0) === 127) || !wellFormedUnicode(input.value)) message = "Use a username without control or incomplete surrogate characters.";
    }
    if (input.name === "password" || input.name === "confirmation") {
      const count = Array.from(input.value).length;
      if (count < (signup ? 12 : 1) || count > 256) message = signup ? "Use 12–256 Unicode characters." : "Use 1–256 Unicode characters.";
      // eslint-disable-next-line no-control-regex -- Intentional C0 whitespace in the signup-only blank policy.
      if (signup && !/[^\s\p{White_Space}\u001c-\u001f]/u.test(input.value)) message = "Your password must contain at least one non-whitespace character.";
      if (signup && !wellFormedUnicode(input.value)) message = "Use well-formed Unicode characters; incomplete surrogate characters are not allowed.";
      if (input.name === "confirmation" && input.value !== password) message = "Passwords must match exactly, including spaces.";
    }

    input.setCustomValidity(message);
  }

  function guardPasswordPaste(event) {
    // Single-line password inputs silently discard CR/LF from pasted secrets.
    if (/[\r\n]/.test(event.clipboardData.getData("text"))) {
      event.preventDefault();
      clearSecrets();
      event.currentTarget.setCustomValidity("Passwords containing line breaks cannot be entered in this form.");
      setError("The password was not pasted because it contains line breaks. No changed password was submitted.");
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (loading || completed) return;
    const element = event.currentTarget;
    for (const input of element.querySelectorAll('input')) validateField({ currentTarget: input });
    if (!element.reportValidity()) return;
    request.current.controller?.abort();
    const controller = new AbortController();
    const sequence = ++request.current.sequence;
    request.current.controller = controller;
    const isCurrent = () => request.current.mounted && request.current.sequence === sequence && !controller.signal.aborted;
    setError("");
    setLoading(true);
    try {
      // HTML password inputs can strip line breaks. Do not submit a changed secret.
      if (element.elements.namedItem("password").value !== password || signup && element.elements.namedItem("confirmation").value !== confirmation) {
        clearSecrets();
        setError("The browser changed the password field. Re-enter your password exactly; line breaks cannot be entered here.");
        return;
      }
      const input = { username: signup ? username.toLowerCase() : username, password };
      const options = { signal: controller.signal };
      const result = signup
        ? await accountApi.signup(input, options)
        : await signIn(input, options);
      if (!isCurrent()) return;
      clearSecrets();
      setLoading(false);
      setCompleted(true);
      request.current.sequence += 1;
      request.current.controller = null;
      await onSuccess?.(result);
    } catch (failure) {
      if (isCurrent() && failure?.name !== "AbortError") {
        clearSecrets();
        setError(signup ? accountErrorMessage(failure) : signInErrorMessage(failure));
      }
    } finally {
      if (isCurrent()) {
        request.current.controller = null;
        setLoading(false);
      }
    }
  }

  return (
    <main className="account-auth">
      <section className="account-auth__card" aria-labelledby={`${prefix}-title`}>
        <div className="account-auth__brand" aria-hidden="true">AP</div>
        <p className="account-auth__eyebrow">PROJECTFLOW AI · YOUR WORKSPACE</p>
        <h1 id={`${prefix}-title`}>{signup ? "Create your account" : "Welcome back"}</h1>
        <p className="account-auth__intro">{signup ? "Create a user account to get started. Administrator access is never granted automatically."
          : "Sign in to your account. Your verified server-assigned role determines your workspace."}</p>
        {registrationNotice && <p className="account-auth__notice" role="status">Your account was created. Sign in with your new credentials.</p>}
        {completed && <p className="account-auth__notice" role="status">{signup ? "Account created. Continue to sign in; registration does not start a session." : "Account verified. Opening your workspace."}</p>}
        <form ref={form} onSubmit={submit} className="account-auth__form" aria-busy={loading} onInvalid={event => event.target.setAttribute("aria-invalid", "true")} onInput={event => event.target.removeAttribute("aria-invalid")}>
          <fieldset disabled={loading || completed}>
            <legend className="account-auth__sr-only">{signup ? "Registration details" : "Sign-in details"}</legend>
            <label htmlFor={`${prefix}-username`}>Username</label>
            <input id={`${prefix}-username`} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required minLength={signup ? 3 : 1} maxLength={signup ? 50 : 200} pattern={signup ? "[a-z0-9][a-z0-9_.\\-]{2,49}" : undefined} value={username} onChange={event => { event.target.setCustomValidity(""); setUsername(signup ? event.target.value.toLowerCase() : event.target.value); setError(""); }} onBlur={validateField} aria-describedby={`${prefix}-username-hint`} />
            <p id={`${prefix}-username-hint`} className="account-auth__hint">{signup ? usernameHint : loginUsernameHint}</p>
            <label htmlFor={`${prefix}-password`}>Password</label>
            <input id={`${prefix}-password`} name="password" type="password" onPaste={guardPasswordPaste} autoComplete={signup ? "new-password" : "current-password"} required maxLength={512} value={password} onChange={event => { event.target.setCustomValidity(""); setPassword(event.target.value); const confirm = form.current?.elements.namedItem("confirmation"); confirm?.setCustomValidity(""); setError(""); }} onBlur={validateField} aria-describedby={`${prefix}-password-hint`} />
            <p id={`${prefix}-password-hint`} className="account-auth__hint">{signup ? passwordHint : "1–256 Unicode characters. Spaces are kept exactly."}</p>
            {signup && <>
              <label htmlFor={`${prefix}-confirmation`}>Confirm password</label>
              <input id={`${prefix}-confirmation`} name="confirmation" type="password" onPaste={guardPasswordPaste} autoComplete="new-password" required maxLength={512} value={confirmation} onChange={event => { event.target.setCustomValidity(""); setConfirmation(event.target.value); }} onBlur={validateField} aria-describedby={`${prefix}-confirm-hint`} />
              <p id={`${prefix}-confirm-hint`} className="account-auth__hint">Re-enter your password exactly, including spaces.</p>
            </>}

            {error && <p className="account-auth__error" role="alert">{error}</p>}
            <button className="account-auth__primary" type="submit">{loading ? (signup ? "Creating account…" : "Verifying sign-in…") : signup ? "Create account" : "Sign in"}</button>
          </fieldset>
          {loading && <p className="account-auth__hint" role="status">{signup ? "Submitting registration. No session is created." : "Checking your credentials and session…"}</p>}
        </form>
        <nav className="account-auth__links" aria-label="Account options">
          <p>{signup ? "Already have an account? " : "New to ProjectFlow? "}<Link to={signup ? "/login" : "/signup"} onClick={leave}>{signup ? "Sign in" : "Create a user account"}</Link></p>
        </nav>
        {onBack && <button className="account-auth__quiet" type="button" onClick={back}>Back to home</button>}
      </section>
    </main>
  );
}

export function UserLogin({ onLoginSuccess, onBack, registrationNotice }) {
  return <AccountForm key="login" mode="login" onSuccess={onLoginSuccess} onBack={onBack} registrationNotice={registrationNotice} />;
}

export function UserSignup({ onSignupSuccess, onBack }) {
  return <AccountForm key="signup" mode="signup" onSuccess={onSignupSuccess} onBack={onBack} />;
}
