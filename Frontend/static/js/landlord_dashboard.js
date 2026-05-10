'use strict';

// ── Sidebar toggle ───────────────────────────────────────────
const toggleBtn = document.getElementById('sidebar-toggle');
const sidebar   = document.getElementById('sidebar');
if (toggleBtn && sidebar) {
  toggleBtn.addEventListener('click', () => {
    if (window.innerWidth <= 768) {
      sidebar.classList.toggle('mobile-open');
    } else {
      sidebar.classList.toggle('collapsed');
    }
  });
  document.addEventListener('click', e => {
    if (window.innerWidth <= 768 &&
        !sidebar.contains(e.target) &&
        !toggleBtn.contains(e.target)) {
      sidebar.classList.remove('mobile-open');
    }
  });
}

// ── Delete modal ─────────────────────────────────────────────
let pendingDeleteId = null;

function confirmDelete(pid, name) {
  pendingDeleteId = pid;
  document.getElementById('modal-prop-name').textContent = name;
  document.getElementById('delete-modal').style.display = 'flex';
}

function closeModal() {
  pendingDeleteId = null;
  document.getElementById('delete-modal').style.display = 'none';
}

document.getElementById('modal-confirm-btn')?.addEventListener('click', async () => {
  if (!pendingDeleteId) return;
  const btn = document.getElementById('modal-confirm-btn');
  btn.textContent = 'Deleting…';
  btn.disabled = true;

  try {
    const res = await fetch(`/landlord/property/${pendingDeleteId}/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (res.ok) {
      const card = document.getElementById(`prop-${pendingDeleteId}`);
      if (card) {
        card.style.transition = 'opacity .3s, transform .3s';
        card.style.opacity    = '0';
        card.style.transform  = 'scale(.95)';
        setTimeout(() => card.remove(), 300);
      }
      closeModal();
      showToast('Property deleted successfully.', 'success');
    } else {
      showToast('Failed to delete. Please try again.', 'error');
    }
  } catch {
    showToast('Network error. Please try again.', 'error');
  } finally {
    btn.textContent = 'Yes, Delete';
    btn.disabled = false;
  }
});

// Close modal on overlay click
document.getElementById('delete-modal')?.addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

// ── Toast notifications ──────────────────────────────────────
function showToast(msg, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `flash flash-${type}`;
  toast.style.cssText = `
    position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;
    min-width:260px;max-width:380px;
    animation:slideIn .3s ease;
  `;
  toast.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
    ${msg}`;

  const style = document.createElement('style');
  style.textContent = '@keyframes slideIn{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}';
  document.head.appendChild(style);

  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = 'opacity .4s';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 400);
  }, 3500);
}
