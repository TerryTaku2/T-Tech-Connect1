'use strict';

// ── Tab switching ─────────────────────────────────────────
function switchTab(tab) {
  document.getElementById('panel-login').style.display    = tab === 'login'    ? '' : 'none';
  document.getElementById('panel-register').style.display = tab === 'register' ? '' : 'none';
  document.getElementById('tab-login').classList.toggle('active',    tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
  document.getElementById('js-alert').style.display = 'none';
  if (tab === 'login')    document.getElementById('email').focus();
  if (tab === 'register') document.getElementById('reg-name').focus();
}

// Open register tab if URL hash says so
if (location.hash === '#register') switchTab('register');

// ── Shared helpers ────────────────────────────────────────
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function isValidEmail(v) { return /^[\w.\-+]+@[\w.\-]+\.\w{2,}$/.test(v.trim()); }
function isValidPhone(v) { return /^\+?[\d\s\-]{7,15}$/.test(v.trim()); }
function isEmailOrPhone(v) { return v.includes('@') ? isValidEmail(v) : isValidPhone(v); }

function showAlert(msg, type = 'error') {
  const el = document.getElementById('js-alert');
  el.className = `alert alert-${type}`;
  el.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      ${type === 'error'
        ? '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
        : '<circle cx="12" cy="12" r="10"/><polyline points="20 6 9 17 4 12"/>'}
    </svg>${escapeHtml(msg)}`;
  el.style.display = '';
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function setFieldError(input, errId, msg) {
  if (input) input.classList.toggle('is-invalid', !!msg);
  const el = document.getElementById(errId);
  if (el) el.textContent = msg || '';
}

function strengthScore(val) {
  let s = 0;
  if (val.length >= 8)          s++;
  if (val.length >= 12)         s++;
  if (/[A-Z]/.test(val))        s++;
  if (/[0-9]/.test(val))        s++;
  if (/[^A-Za-z0-9]/.test(val)) s++;
  return s;
}
function applyStrength(score, fillId, labelId, wrapId) {
  const wrap  = document.getElementById(wrapId);
  const fill  = document.getElementById(fillId);
  const label = document.getElementById(labelId);
  if (!wrap) return;
  if (!score) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'flex';
  const levels = [
    { pct:'20%', color:'#dc2626', label:'Very weak'   },
    { pct:'40%', color:'#ea580c', label:'Weak'         },
    { pct:'60%', color:'#ca8a04', label:'Fair'         },
    { pct:'80%', color:'#16a34a', label:'Strong'       },
    { pct:'100%',color:'#15803d', label:'Very strong'  },
  ];
  const l = levels[Math.min(score - 1, 4)];
  fill.style.width      = l.pct;
  fill.style.background = l.color;
  label.textContent     = l.label;
  label.style.color     = l.color;
}

function setLoading(btn, textEl, spinEl, on) {
  btn.disabled              = on;
  textEl.style.display      = on ? 'none' : '';
  spinEl.style.display      = on ? ''     : 'none';
}

// ── LOGIN FORM ────────────────────────────────────────────
const loginForm  = document.getElementById('login-form');
const emailInput = document.getElementById('email');
const pwdInput   = document.getElementById('password');
const rememberChk= document.getElementById('remember');
const submitBtn  = document.getElementById('btn-submit');

document.getElementById('toggle-pwd').addEventListener('click', () => {
  const show = pwdInput.type === 'text';
  pwdInput.type = show ? 'password' : 'text';
  document.getElementById('eye-icon').style.display     = show ? '' : 'none';
  document.getElementById('eye-off-icon').style.display = show ? 'none' : '';
  pwdInput.focus();
});

emailInput.addEventListener('blur',  () => {
  const v = emailInput.value.trim();
  setFieldError(emailInput, 'email-error', v && !isEmailOrPhone(v) ? 'Please enter a valid email address or phone number.' : '');
});
emailInput.addEventListener('input', () => setFieldError(emailInput, 'email-error', ''));
pwdInput.addEventListener('blur',    () => setFieldError(pwdInput, 'password-error', !pwdInput.value ? 'Password is required.' : ''));
pwdInput.addEventListener('input',   () => setFieldError(pwdInput, 'password-error', ''));

// Restore remembered email
(function () {
  const saved = localStorage.getItem('ttc_email');
  if (saved) { emailInput.value = saved; rememberChk.checked = true; }
})();

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  document.getElementById('js-alert').style.display = 'none';
  const email    = emailInput.value.trim();
  const password = pwdInput.value;
  const remember = rememberChk.checked;
  let ok = true;

  if (!email) { setFieldError(emailInput, 'email-error', 'Email or phone number is required.'); ok = false; }
  else if (!isEmailOrPhone(email)) { setFieldError(emailInput, 'email-error', 'Please enter a valid email address or phone number.'); ok = false; }
  if (!password) { setFieldError(pwdInput, 'password-error', 'Password is required.'); ok = false; }
  if (!ok) return;

  if (remember) localStorage.setItem('ttc_email', email);
  else localStorage.removeItem('ttc_email');

  const btnText = submitBtn.querySelector('.btn-text');
  const btnSpin = submitBtn.querySelector('.btn-spinner');
  setLoading(submitBtn, btnText, btnSpin, true);

  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, remember }),
    });
    const ct = res.headers.get('Content-Type') || '';
    if (!ct.includes('application/json')) { _nativeLogin(email, password, remember); return; }
    const data = await res.json();
    if (res.ok && data.success) {
      submitBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Redirecting…`;
      submitBtn.style.background = 'linear-gradient(135deg,#16a34a,#15803d)';
      setTimeout(() => { window.location.href = data.redirect || '/dashboard'; }, 600);
    } else {
      showAlert(data.error || 'Invalid email or password.');
      pwdInput.value = ''; pwdInput.focus();
      setLoading(submitBtn, btnText, btnSpin, false);
      loginForm.classList.add('shake');
      setTimeout(() => loginForm.classList.remove('shake'), 500);
    }
  } catch {
    _nativeLogin(email, password, remember);
  }
});

function _nativeLogin(email, password, remember) {
  const f = document.createElement('form');
  f.method = 'POST'; f.action = '/login';
  [['email', email], ['password', password], ['remember', remember ? '1' : '']].forEach(([n, v]) => {
    const i = document.createElement('input'); i.type = 'hidden'; i.name = n; i.value = v; f.appendChild(i);
  });
  document.body.appendChild(f); f.submit();
}

// ── REGISTER FORM ─────────────────────────────────────────
const regForm    = document.getElementById('register-form');
const regName    = document.getElementById('reg-name');
const regEmail   = document.getElementById('reg-email');
const regPhone   = document.getElementById('reg-phone');
const regPwd     = document.getElementById('reg-password');
const regConfirm = document.getElementById('reg-confirm');
const regBtn     = document.getElementById('btn-register');

document.getElementById('toggle-reg-pwd').addEventListener('click', () => {
  const show = regPwd.type === 'text';
  regPwd.type = show ? 'password' : 'text';
  document.getElementById('reg-eye-icon').style.display     = show ? '' : 'none';
  document.getElementById('reg-eye-off-icon').style.display = show ? 'none' : '';
  regPwd.focus();
});

regPwd.addEventListener('input', () => {
  applyStrength(regPwd.value ? strengthScore(regPwd.value) : 0,
    'reg-strength-fill', 'reg-strength-label', 'reg-strength-wrap');
  setFieldError(regPwd, 'reg-password-error', '');
});

// Highlight selected role card
document.querySelectorAll('.role-radio').forEach(radio => {
  radio.addEventListener('change', () => {
    document.getElementById('role-card-student').classList.toggle('selected', document.querySelector('[value="student"]').checked);
    document.getElementById('role-card-landlord').classList.toggle('selected', document.querySelector('[value="landlord"]').checked);
  });
});

regForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  document.getElementById('js-alert').style.display = 'none';

  const name     = regName.value.trim();
  const email    = regEmail.value.trim().toLowerCase();
  const phone    = regPhone ? regPhone.value.trim() : '';
  const role     = document.querySelector('input[name="reg-role"]:checked')?.value || '';
  const password = regPwd.value;
  const confirm  = regConfirm.value;
  let ok = true;

  setFieldError(regName,    'reg-name-error',     '');
  setFieldError(regEmail,   'reg-email-error',    '');
  setFieldError(regPwd,     'reg-password-error', '');
  setFieldError(regConfirm, 'reg-confirm-error',  '');
  setFieldError(regPhone,   'reg-phone-error',    '');

  if (!name) { setFieldError(regName, 'reg-name-error', 'Full name is required.'); ok = false; }
  if (!email) { setFieldError(regEmail, 'reg-email-error', 'Email address is required.'); ok = false; }
  else if (!isValidEmail(email)) { setFieldError(regEmail, 'reg-email-error', 'Please enter a valid email address.'); ok = false; }
  if (phone && !isValidPhone(phone)) { setFieldError(regPhone, 'reg-phone-error', 'Please enter a valid phone number.'); ok = false; }
  if (!role) { setFieldError(null, 'reg-role-error', 'Please select your account type.'); ok = false; }
  if (!password || password.length < 8) { setFieldError(regPwd, 'reg-password-error', 'Password must be at least 8 characters.'); ok = false; }
  if (password !== confirm) { setFieldError(regConfirm, 'reg-confirm-error', 'Passwords do not match.'); ok = false; }
  if (!ok) return;

  const btnText = regBtn.querySelector('.btn-text');
  const btnSpin = regBtn.querySelector('.btn-spinner');
  setLoading(regBtn, btnText, btnSpin, true);

  try {
    const res  = await fetch('/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ full_name: name, email, phone, password, role }),
    });
    const data = await res.json();
    if (res.ok && data.success) {
      regBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Account created! Redirecting…`;
      regBtn.style.background = 'linear-gradient(135deg,#16a34a,#15803d)';
      setTimeout(() => { window.location.href = data.redirect || '/dashboard'; }, 800);
    } else {
      showAlert(data.error || 'Registration failed. Please try again.');
      setLoading(regBtn, btnText, btnSpin, false);
    }
  } catch {
    showAlert('Network error. Please check your connection and try again.');
    setLoading(regBtn, btnText, btnSpin, false);
  }
});

// ── Shake animation ───────────────────────────────────────
const s = document.createElement('style');
s.textContent = `
  @keyframes shake { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-6px)} 40%{transform:translateX(6px)} 60%{transform:translateX(-4px)} 80%{transform:translateX(4px)} }
  .shake { animation: shake .4s ease; }`;
document.head.appendChild(s);

window.addEventListener('DOMContentLoaded', () => {
  (emailInput.value ? pwdInput : emailInput).focus();
});
