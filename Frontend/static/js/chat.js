'use strict';

// ── State ────────────────────────────────────────────────────
let socket;
let activeConvId   = null;
let conversations  = [];
let typingTimer    = null;
let isTyping       = false;
let lastDateLabel  = null;

// ── DOM refs ─────────────────────────────────────────────────
const convList       = document.getElementById('conv-list');
const convSearch     = document.getElementById('conv-search');
const convPanel      = document.getElementById('conv-panel');
const threadEmpty    = document.getElementById('thread-empty');
const threadActive   = document.getElementById('thread-active');
const threadAvatar   = document.getElementById('thread-avatar');
const threadName     = document.getElementById('thread-contact-name');
const threadSub      = document.getElementById('thread-contact-sub');
const threadPropLink = document.getElementById('thread-prop-link');
const msgList        = document.getElementById('messages-list');
const msgInput       = document.getElementById('msg-input');
const sendBtn        = document.getElementById('send-btn');
const typingEl       = document.getElementById('typing-indicator');
const typingLabel    = document.getElementById('typing-label');
const backBtn        = document.getElementById('back-btn');
const messagesArea   = document.getElementById('messages-area');
const messagesLoading = document.getElementById('messages-loading');

// ── Init ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initSocket();
  loadConversations();

  if (window.OPEN_CONV_ID) {
    // Will be opened after conversations load
  }

  // Sidebar toggle (topbar hamburger)
  const tog = document.getElementById('sidebar-toggle');
  if (tog) tog.addEventListener('click', () => convPanel.classList.toggle('hidden'));

  // Back button (mobile)
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      convPanel.classList.remove('hidden');
    });
  }

  // Search filter
  convSearch.addEventListener('input', filterConversations);

  // Input events
  msgInput.addEventListener('input', onInputChange);
  msgInput.addEventListener('keydown', onInputKeydown);
  sendBtn.addEventListener('click', sendMessage);
});

// ── Socket.IO ────────────────────────────────────────────────
function initSocket() {
  // polling first — works with Flask's standard dev server (app.run).
  // Socket.IO upgrades to WebSocket automatically if the server supports it.
  socket = io({ transports: ['polling', 'websocket'] });

  socket.on('connect', () => {
    if (activeConvId) socket.emit('join_conv', { conv_id: activeConvId });
  });

  socket.on('new_msg', (msg) => {
    if (msg.conversation_id !== activeConvId) {
      // Increment unread badge in conversation list
      bumpUnread(msg.conversation_id);
      updateLastMsg(msg.conversation_id, msg.content, msg.sent_at, false);
      return;
    }
    appendMessage(msg, false);
    scrollToBottom();
    markRead(activeConvId);
    updateLastMsg(activeConvId, msg.content, msg.sent_at, false);
  });

  socket.on('typing_update', (data) => {
    if (data.typing) {
      typingLabel.textContent = `${data.user_name} is typing…`;
      typingEl.style.display = 'flex';
      scrollToBottom();
    } else {
      typingEl.style.display = 'none';
    }
  });

  socket.on('disconnect', () => {});
}

// ── Load conversations ────────────────────────────────────────
async function loadConversations() {
  try {
    const res  = await fetch('/api/conversations');
    conversations = await res.json();
    renderConversations(conversations);

    if (window.OPEN_CONV_ID) {
      openConversation(window.OPEN_CONV_ID);
    }
  } catch (e) {
    convList.innerHTML = '<div class="conv-empty">Failed to load conversations.</div>';
  }
}

function renderConversations(list) {
  if (!list.length) {
    convList.innerHTML = `
      <div class="conv-empty">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <p>No conversations yet.<br>Start one from a property listing.</p>
      </div>`;
    return;
  }

  convList.innerHTML = list.map(c => convItemHTML(c)).join('');
  convList.querySelectorAll('.conv-item').forEach(el => {
    el.addEventListener('click', () => openConversation(parseInt(el.dataset.convId)));
  });
}

function convItemHTML(c) {
  const initials = (c.other_name || '?')[0].toUpperCase();
  const roleClass = `av-${c.other_role || 'default'}`;
  const isOnline  = isRecentlySeen(c.other_last_seen);
  const lastMsg   = c.last_msg
    ? (c.last_sender_id === window.CURRENT_USER_ID ? `You: ${c.last_msg}` : c.last_msg)
    : 'No messages yet';
  const isMine    = c.last_sender_id === window.CURRENT_USER_ID;
  const time      = c.last_msg_time ? formatTime(c.last_msg_time) : '';
  const unread    = c.unread > 0 ? `<span class="unread-badge">${c.unread}</span>` : '';
  const propLabel = c.property_title ? `<div style="font-size:.7rem;color:var(--text-muted);margin-top:.1rem">🏠 ${c.property_title}</div>` : '';

  return `
    <div class="conv-item" data-conv-id="${c.id}" id="conv-item-${c.id}">
      <div class="conv-avatar ${roleClass}">
        ${initials}
        ${isOnline ? '<span class="online-dot"></span>' : ''}
      </div>
      <div class="conv-content">
        <div class="conv-row1">
          <span class="conv-name">${escHtml(c.other_name)}</span>
          <span class="conv-time">${time}</span>
        </div>
        ${propLabel}
        <div class="conv-row2">
          <span class="conv-last-msg ${isMine ? 'mine' : ''}">${escHtml(lastMsg.slice(0,60))}${lastMsg.length > 60 ? '…' : ''}</span>
          ${unread}
        </div>
      </div>
    </div>`;
}

// ── Open conversation ─────────────────────────────────────────
async function openConversation(convId) {
  // Leave previous room
  if (activeConvId && socket) socket.emit('leave_conv', { conv_id: activeConvId });

  activeConvId = convId;

  // Mark active in list
  document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
  const item = document.getElementById(`conv-item-${convId}`);
  if (item) {
    item.classList.add('active');
    // Clear unread badge
    const badge = item.querySelector('.unread-badge');
    if (badge) badge.remove();
  }

  // On mobile: hide conv panel, show thread
  if (window.innerWidth <= 768) convPanel.classList.add('hidden');

  // Show active thread, populate header
  threadEmpty.style.display  = 'none';
  threadActive.style.display = 'flex';

  const conv = conversations.find(c => c.id === convId);
  if (conv) {
    const initials  = (conv.other_name || '?')[0].toUpperCase();
    const isOnline  = isRecentlySeen(conv.other_last_seen);
    const roleClass = `av-${conv.other_role || 'default'}`;

    threadAvatar.textContent = initials;
    threadAvatar.className   = `thread-avatar ${roleClass}`;
    threadName.textContent   = conv.other_name;
    threadSub.textContent    = isOnline ? '● Online' : 'Offline';
    threadSub.className      = `thread-contact-sub ${isOnline ? 'online' : ''}`;

    if (conv.property_id && conv.property_title) {
      threadPropLink.style.display  = 'inline-flex';
      threadPropLink.href           = `/landlord/property/${conv.property_id}`;
      threadPropLink.childNodes[threadPropLink.childNodes.length - 1].textContent = ` ${conv.property_title}`;
    } else {
      threadPropLink.style.display = 'none';
    }
  }

  // Load messages
  msgList.innerHTML        = '';
  messagesLoading.style.display = 'flex';
  lastDateLabel = null;

  try {
    const res  = await fetch(`/api/messages/${convId}`);
    const msgs = await res.json();
    messagesLoading.style.display = 'none';

    msgs.forEach(m => appendMessage(m, true));
    scrollToBottom(false);
  } catch {
    messagesLoading.style.display = 'none';
    msgList.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:2rem">Failed to load messages.</div>';
  }

  // Join Socket.IO room
  if (socket) socket.emit('join_conv', { conv_id: convId });

  msgInput.focus();
}

// ── Append a message bubble ───────────────────────────────────
function appendMessage(msg, isHistory) {
  const isMine  = msg.sender_id === window.CURRENT_USER_ID;
  const dateStr = formatDateLabel(msg.sent_at);

  // Date separator
  if (dateStr !== lastDateLabel) {
    lastDateLabel = dateStr;
    const sep = document.createElement('div');
    sep.className = 'date-sep';
    sep.innerHTML = `<span>${dateStr}</span>`;
    msgList.appendChild(sep);
  }

  const time = formatTime(msg.sent_at);

  const row = document.createElement('div');
  row.className = `msg-row ${isMine ? 'mine' : 'theirs'}`;
  row.dataset.msgId = msg.id;

  const tick = isMine ? `
    <svg class="tick-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>` : '';

  row.innerHTML = `
    <div class="msg-bubble">${escHtml(msg.content)}</div>
    <div class="msg-time">${time}${tick}</div>`;

  msgList.appendChild(row);
}

// ── Send message ──────────────────────────────────────────────
function sendMessage() {
  if (!activeConvId) return;
  const content = msgInput.value.trim();
  if (!content) return;

  socket.emit('send_msg', { conv_id: activeConvId, content });

  // Optimistic — add immediately
  const now = new Date().toISOString().replace('T', ' ').slice(0, 19);
  appendMessage({
    id: Date.now(),
    conversation_id: activeConvId,
    sender_id: window.CURRENT_USER_ID,
    content,
    sent_at: now,
    sender_name: window.CURRENT_USER_NAME,
  }, false);
  scrollToBottom();
  updateLastMsg(activeConvId, content, now, true);

  msgInput.value = '';
  msgInput.style.height = 'auto';
  sendBtn.disabled = true;
  stopTyping();
}

// ── Typing events ─────────────────────────────────────────────
function onInputChange() {
  autoResize(msgInput);
  sendBtn.disabled = !msgInput.value.trim();

  if (!isTyping && activeConvId && socket) {
    isTyping = true;
    socket.emit('typing', { conv_id: activeConvId, typing: true });
  }
  clearTimeout(typingTimer);
  typingTimer = setTimeout(stopTyping, 2000);
}

function stopTyping() {
  if (isTyping && activeConvId && socket) {
    isTyping = false;
    socket.emit('typing', { conv_id: activeConvId, typing: false });
  }
}

function onInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled) sendMessage();
  }
}

// ── Helpers ───────────────────────────────────────────────────
function scrollToBottom(smooth = true) {
  requestAnimationFrame(() => {
    messagesArea.scrollTo({ top: messagesArea.scrollHeight, behavior: smooth ? 'smooth' : 'instant' });
  });
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

function markRead(convId) {
  fetch(`/api/conversations/${convId}/read`, { method: 'POST' }).catch(() => {});
}

function bumpUnread(convId) {
  const item = document.getElementById(`conv-item-${convId}`);
  if (!item) return;
  let badge = item.querySelector('.unread-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'unread-badge';
    badge.textContent = '1';
    item.querySelector('.conv-row2')?.appendChild(badge);
  } else {
    badge.textContent = parseInt(badge.textContent || 0) + 1;
  }
}

function updateLastMsg(convId, content, time, isMine) {
  const item = document.getElementById(`conv-item-${convId}`);
  if (!item) return;
  const msgEl = item.querySelector('.conv-last-msg');
  const timeEl = item.querySelector('.conv-time');
  if (msgEl) {
    const preview = (isMine ? 'You: ' : '') + content;
    msgEl.textContent = preview.slice(0, 60) + (preview.length > 60 ? '…' : '');
    msgEl.className = `conv-last-msg ${isMine ? 'mine' : ''}`;
  }
  if (timeEl && time) timeEl.textContent = formatTime(time);

  // Move to top of list
  const list = document.getElementById('conv-list');
  if (list && item.parentNode === list) list.prepend(item);
}

function filterConversations() {
  const q = convSearch.value.toLowerCase();
  document.querySelectorAll('.conv-item').forEach(el => {
    const name = el.querySelector('.conv-name')?.textContent?.toLowerCase() || '';
    const msg  = el.querySelector('.conv-last-msg')?.textContent?.toLowerCase() || '';
    el.style.display = (name.includes(q) || msg.includes(q)) ? '' : 'none';
  });
}

function isRecentlySeen(lastSeen) {
  if (!lastSeen) return false;
  return (Date.now() - new Date(lastSeen + ' UTC').getTime()) < 5 * 60 * 1000;
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts.includes('T') ? ts : ts + 'Z');
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());

  if (msgDay.getTime() === today.getTime()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (msgDay.getTime() === yesterday.getTime()) return 'Yesterday';
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

function formatDateLabel(ts) {
  if (!ts) return 'Today';
  const d = new Date(ts.includes('T') ? ts : ts + 'Z');
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());

  if (msgDay.getTime() === today.getTime()) return 'Today';
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (msgDay.getTime() === yesterday.getTime()) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

function escHtml(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])
  );
}

// ── Global: start conversation from external page ─────────────
window.startConversation = async function(recipientId, propertyId, propertyTitle) {
  try {
    const res = await fetch('/api/conversations/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        recipient_id: recipientId,
        property_id: propertyId,
        subject: `Inquiry: ${propertyTitle}`,
      }),
    });
    const data = await res.json();
    if (data.conv_id) window.location.href = `/messages?c=${data.conv_id}`;
    else alert('Could not start conversation. Please try again.');
  } catch {
    alert('Network error. Please try again.');
  }
};

// ── Periodic unread count refresh for nav badge ───────────────
function refreshUnreadBadge() {
  fetch('/api/messages/unread-count')
    .then(r => r.json())
    .then(({ count }) => {
      document.querySelectorAll('.nav-unread').forEach(el => {
        el.textContent = count;
        el.style.display = count > 0 ? 'flex' : 'none';
      });
    })
    .catch(() => {});
}
setInterval(refreshUnreadBadge, 15000);
