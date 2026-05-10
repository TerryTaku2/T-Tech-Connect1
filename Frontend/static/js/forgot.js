'use strict';

const form     = document.getElementById('forgot-form');
const emailEl  = document.getElementById('email');
const emailErr = document.getElementById('email-error');
const jsAlert  = document.getElementById('js-alert');
const submitBtn = document.getElementById('btn-submit');
const btnText   = submitBtn.querySelector('.btn-text');
const btnSpinner = submitBtn.querySelector('.btn-spinner');

function setLoading(on) {
  submitBtn.disabled = on;
  btnText.style.display    = on ? 'none' : '';
  btnSpinner.style.display = on ? ''     : 'none';
}

function showAlert(msg, type = 'info') {
  jsAlert.className = `alert alert-${type}`;
  jsAlert.textContent = msg;
  jsAlert.style.display = '';
}

emailEl.addEventListener('blur', () => {
  const v = emailEl.value.trim();
  if (v && !/^[\w.\-+]+@[\w.\-]+\.\w{2,}$/.test(v)) {
    emailEl.classList.add('is-invalid');
    emailErr.textContent = 'Please enter a valid email address.';
  } else {
    emailEl.classList.remove('is-invalid');
    emailErr.textContent = '';
  }
});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = emailEl.value.trim();

  if (!email) {
    emailEl.classList.add('is-invalid');
    emailErr.textContent = 'Email address is required.';
    return;
  }

  setLoading(true);

  try {
    const res = await fetch('/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    showAlert(data.message || 'If that email is registered, a reset link has been sent.', 'success');
    form.reset();
  } catch {
    showAlert('Connection error. Please try again.', 'error');
  } finally {
    setLoading(false);
  }
});
