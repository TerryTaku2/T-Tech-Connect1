'use strict';

// ── DOM refs ────────────────────────────────────────────────
const form        = document.getElementById('login-form');
const emailInput  = document.getElementById('email');
const pwdInput    = document.getElementById('password');
const toggleBtn   = document.getElementById('toggle-pwd');
const eyeIcon     = document.getElementById('eye-icon');
const eyeOffIcon  = document.getElementById('eye-off-icon');
const rememberChk = document.getElementById('remember');
const submitBtn   = document.getElementById('btn-submit');
const btnText     = submitBtn.querySelector('.btn-text');
const btnSpinner  = submitBtn.querySelector('.btn-spinner');
const jsAlert     = document.getElementById('js-alert');
const emailErr    = document.getElementById('email-error');
const pwdErr      = document.getElementById('password-error');
const strengthWrap = document.getElementById('strength-wrap');
const strengthFill = document.getElementById('strength-fill');
const strengthLabel = document.getElementById('strength-label');

// ── Helpers ──────────────────────────────────────────────────
function setLoading(on) {
  submitBtn.disabled = on;
  btnText.style.display    = on ? 'none'  : '';
  btnSpinner.style.display = on ? ''      : 'none';
}

function showAlert(msg, type = 'error') {
  jsAlert.className = `alert alert-${type}`;
  jsAlert.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      ${type === 'error'
        ? '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
        : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'}
    </svg>
    ${escapeHtml(msg)}`;
  jsAlert.style.display = '';
  jsAlert.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideAlert() { jsAlert.style.display = 'none'; }

function setFieldError(input, errEl, msg) {
  input.classList.toggle('is-invalid', !!msg);
  errEl.textContent = msg || '';
}

function clearErrors() {
  setFieldError(emailInput, emailErr, '');
  setFieldError(pwdInput,   pwdErr,   '');
  hideAlert();
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

function isValidEmail(v) {
  return /^[\w.\-+]+@[\w.\-]+\.\w{2,}$/.test(v.trim());
}

// ── Password visibility toggle ────────────────────────────
toggleBtn.addEventListener('click', () => {
  const showing = pwdInput.type === 'text';
  pwdInput.type = showing ? 'password' : 'text';
  eyeIcon.style.display    = showing ? ''   : 'none';
  eyeOffIcon.style.display = showing ? 'none' : '';
  toggleBtn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
  pwdInput.focus();
});

// ── Password strength meter ──────────────────────────────
pwdInput.addEventListener('input', () => {
  const val = pwdInput.value;
  if (!val) { strengthWrap.style.display = 'none'; return; }
  strengthWrap.style.display = 'flex';

  let score = 0;
  if (val.length >= 8)  score++;
  if (val.length >= 12) score++;
  if (/[A-Z]/.test(val)) score++;
  if (/[0-9]/.test(val)) score++;
  if (/[^A-Za-z0-9]/.test(val)) score++;

  const levels = [
    { pct: '20%',  color: '#dc2626', label: 'Very weak' },
    { pct: '40%',  color: '#ea580c', label: 'Weak' },
    { pct: '60%',  color: '#ca8a04', label: 'Fair' },
    { pct: '80%',  color: '#16a34a', label: 'Strong' },
    { pct: '100%', color: '#15803d', label: 'Very strong' },
  ];
  const lvl = levels[Math.min(score, 4)];
  strengthFill.style.width      = lvl.pct;
  strengthFill.style.background = lvl.color;
  strengthLabel.textContent     = lvl.label;
  strengthLabel.style.color     = lvl.color;
});

// ── Real-time inline validation ──────────────────────────
emailInput.addEventListener('blur', () => {
  const v = emailInput.value.trim();
  if (v && !isValidEmail(v)) setFieldError(emailInput, emailErr, 'Please enter a valid email address.');
  else setFieldError(emailInput, emailErr, '');
});

pwdInput.addEventListener('blur', () => {
  if (!pwdInput.value) setFieldError(pwdInput, pwdErr, 'Password is required.');
  else setFieldError(pwdInput, pwdErr, '');
});

emailInput.addEventListener('input', () => setFieldError(emailInput, emailErr, ''));
pwdInput.addEventListener('input',   () => setFieldError(pwdInput,   pwdErr,   ''));

// ── Restore saved email (remember me) ────────────────────
(function restoreEmail() {
  const saved = localStorage.getItem('ttc_email');
  if (saved) { emailInput.value = saved; rememberChk.checked = true; }
})();

// ── Form submission ──────────────────────────────────────
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearErrors();

  const email    = emailInput.value.trim();
  const password = pwdInput.value;
  const remember = rememberChk.checked;
  let valid = true;

  if (!email) {
    setFieldError(emailInput, emailErr, 'Email address is required.');
    valid = false;
  } else if (!isValidEmail(email)) {
    setFieldError(emailInput, emailErr, 'Please enter a valid email address.');
    valid = false;
  }

  if (!password) {
    setFieldError(pwdInput, pwdErr, 'Password is required.');
    valid = false;
  } else if (password.length < 6) {
    setFieldError(pwdInput, pwdErr, 'Password must be at least 6 characters.');
    valid = false;
  }

  if (!valid) return;

  // Persist email for remember-me
  if (remember) localStorage.setItem('ttc_email', email);
  else localStorage.removeItem('ttc_email');

  setLoading(true);

  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, remember }),
    });

    // Guard: if server returned HTML (e.g. redirect), fall back to a form POST
    const contentType = res.headers.get('Content-Type') || '';
    if (!contentType.includes('application/json')) {
      // Server is alive but returned HTML — fall through to native form submit
      _submitFormNative(email, password, remember);
      return;
    }

    const data = await res.json();

    if (res.ok && data.success) {
      btnText.style.display    = 'none';
      btnSpinner.style.display = '';
      submitBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
        Success! Redirecting…`;
      submitBtn.style.background = 'linear-gradient(135deg,#16a34a,#15803d)';
      const dest = data.redirect || (data.role === 'landlord' ? '/landlord' : '/dashboard');
      setTimeout(() => { window.location.href = dest; }, 600);
    } else if (res.status === 429) {
      showAlert(data.error || 'Too many attempts. Please wait before trying again.');
      setLoading(false);
    } else {
      showAlert(data.error || 'Invalid email or password. Please try again.');
      pwdInput.value = '';
      pwdInput.focus();
      setLoading(false);
      form.classList.add('shake');
      setTimeout(() => form.classList.remove('shake'), 500);
    }
  } catch (err) {
    // Network-level failure — fall back to a regular form POST so the user can still log in
    console.warn('AJAX login failed, falling back to form POST:', err);
    _submitFormNative(email, password, remember);
  }
});

// Native form-POST fallback (bypasses AJAX entirely)
function _submitFormNative(email, password, remember) {
  const f = document.createElement('form');
  f.method = 'POST';
  f.action = '/login';
  [['email', email], ['password', password], ['remember', remember ? '1' : '']].forEach(([n, v]) => {
    const inp = document.createElement('input');
    inp.type = 'hidden'; inp.name = n; inp.value = v;
    f.appendChild(inp);
  });
  document.body.appendChild(f);
  f.submit();
});

// ── Shake animation ──────────────────────────────────────
const style = document.createElement('style');
style.textContent = `
  @keyframes shake {
    0%,100%{ transform:translateX(0) }
    20%    { transform:translateX(-6px) }
    40%    { transform:translateX(6px) }
    60%    { transform:translateX(-4px) }
    80%    { transform:translateX(4px) }
  }
  .shake { animation: shake .4s ease; }
`;
document.head.appendChild(style);

// ── Focus first empty field on load ─────────────────────
window.addEventListener('DOMContentLoaded', () => {
  (emailInput.value ? pwdInput : emailInput).focus();
});
