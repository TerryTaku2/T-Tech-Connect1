from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import secrets
import re
import json
import uuid
from datetime import timedelta
from functools import wraps

app = Flask(__name__, template_folder='../Frontend/templates', static_folder='../Frontend/static')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=7)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB total upload limit

GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')
ALLOWED_EXT  = {'jpg', 'jpeg', 'png', 'webp'}
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'Frontend', 'static', 'uploads', 'properties')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'ttech.db')


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        # Safe column migrations for existing databases
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        for col, typedef in [
            ("phone",     "TEXT"),
            ("last_seen", "TIMESTAMP"),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                phone TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                last_seen TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                ip_address TEXT,
                success INTEGER DEFAULT 0,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                landlord_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                property_type TEXT DEFAULT 'apartment',
                description TEXT,
                status TEXT DEFAULT 'available',
                is_shared INTEGER DEFAULT 0,
                total_rooms INTEGER DEFAULT 1,
                available_rooms INTEGER DEFAULT 1,
                bathrooms INTEGER DEFAULT 1,
                price_per_month REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                address TEXT,
                city TEXT,
                country TEXT DEFAULT 'Zimbabwe',
                latitude REAL,
                longitude REAL,
                services TEXT DEFAULT '[]',
                contact_phone TEXT,
                contact_email TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (landlord_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS property_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                is_primary INTEGER DEFAULT 0,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT DEFAULT 'Property Inquiry',
                property_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            );

            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, user_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_deleted INTEGER DEFAULT 0,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (sender_id) REFERENCES users(id)
            );
        """)

        seeds = [
            ("Admin User",     "admin@ttech.ac.zw",    "Admin@1234",    "admin"),
            ("John Student",   "student@ttech.ac.zw",  "Student@1234",  "student"),
            ("Grace Landlord", "landlord@ttech.ac.zw", "Landlord@1234", "landlord"),
        ]
        for name, email, pwd, role in seeds:
            if not conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                conn.execute(
                    "INSERT INTO users (full_name, email, password_hash, role) VALUES (?,?,?,?)",
                    (name, email, generate_password_hash(pwd), role)
                )

        landlord = conn.execute("SELECT id FROM users WHERE email = 'landlord@ttech.ac.zw'").fetchone()
        if landlord:
            if not conn.execute("SELECT id FROM properties WHERE landlord_id = ?", (landlord['id'],)).fetchone():
                conn.execute("""
                    INSERT INTO properties
                        (landlord_id,title,property_type,description,status,is_shared,
                         total_rooms,available_rooms,bathrooms,price_per_month,currency,
                         address,city,country,latitude,longitude,services,contact_phone,contact_email)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    landlord['id'], "Sunshine Student Lodge", "apartment",
                    "A well-furnished, secure student accommodation close to T-Tech campus.",
                    "available", 0, 12, 4, 4, 120.00, "USD",
                    "45 Borrowdale Road, Harare", "Harare", "Zimbabwe",
                    -17.7833, 31.0500,
                    json.dumps(["wifi","water","electricity","security","parking"]),
                    "+263 77 123 4567", "landlord@ttech.ac.zw"
                ))

        conn.commit()


# ── Decorators ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def landlord_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('user_role') not in ('landlord', 'admin'):
            flash('Access denied. Landlord account required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_valid_email(email):
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email)


def log_attempt(email, ip, success):
    with get_db() as conn:
        conn.execute("INSERT INTO login_attempts (email, ip_address, success) VALUES (?,?,?)",
                     (email, ip, 1 if success else 0))
        conn.commit()


def get_failed_attempts(email, ip):
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM login_attempts
               WHERE (email=? OR ip_address=?) AND success=0
               AND attempted_at > datetime('now','-15 minutes')""",
            (email, ip)
        ).fetchone()
        return row['cnt'] if row else 0


def role_redirect(role):
    return {
        'landlord': url_for('landlord_dashboard'),
        'admin':    url_for('dashboard'),
        'student':  url_for('dashboard'),
    }.get(role, url_for('dashboard'))


def get_unread_count(user_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM messages m
            JOIN conversation_members cm
                ON m.conversation_id = cm.conversation_id AND cm.user_id = ?
            WHERE m.sender_id != ?
              AND (m.sent_at > cm.last_read_at OR cm.last_read_at IS NULL)
              AND m.is_deleted = 0
        """, (user_id, user_id)).fetchone()
        return row['cnt'] if row else 0


def _get_own_property(pid):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM properties WHERE id=? AND landlord_id=? AND is_active=1",
            (pid, session['user_id'])
        ).fetchone()


def _get_images(property_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM property_images WHERE property_id=? ORDER BY is_primary DESC, uploaded_at ASC",
            (property_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def _save_images(property_id, files):
    """Persist uploaded image files and record them in DB. First image becomes cover if none set."""
    with get_db() as conn:
        has_cover = conn.execute(
            "SELECT 1 FROM property_images WHERE property_id=? AND is_primary=1", (property_id,)
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM property_images WHERE property_id=?", (property_id,)
        ).fetchone()[0]

        first = True
        for f in files:
            if not f or not f.filename:
                continue
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in ALLOWED_EXT:
                continue
            if count >= 10:
                break
            filename = f'{uuid.uuid4().hex}.{ext}'
            try:
                f.save(os.path.join(UPLOAD_FOLDER, filename))
                is_primary = 1 if (first and not has_cover) else 0
                conn.execute(
                    "INSERT INTO property_images (property_id, filename, is_primary) VALUES (?,?,?)",
                    (property_id, filename, is_primary)
                )
                if first and not has_cover:
                    has_cover = True
                first = False
                count += 1
            except Exception as e:
                app.logger.error(f'Image save error: {e}')
        conn.commit()


def _save_property(pid):
    f = request.form
    errors = {}
    title         = f.get('title', '').strip()
    prop_type     = f.get('property_type', 'apartment')
    description   = f.get('description', '').strip()
    status        = f.get('status', 'available')
    is_shared     = 1 if f.get('is_shared') else 0
    total_rooms   = f.get('total_rooms', '1')
    avail_rooms   = f.get('available_rooms', '1')
    bathrooms     = f.get('bathrooms', '1')
    price         = f.get('price_per_month', '').strip()
    currency      = f.get('currency', 'USD')
    address       = f.get('address', '').strip()
    city          = f.get('city', '').strip()
    country       = f.get('country', 'Zimbabwe').strip()
    lat           = f.get('latitude', '').strip() or None
    lng           = f.get('longitude', '').strip() or None
    services      = json.dumps(f.getlist('services'))
    contact_phone = f.get('contact_phone', '').strip()
    contact_email = f.get('contact_email', '').strip()

    if not title:   errors['title']   = 'Property title is required.'
    if not price:   errors['price']   = 'Monthly price is required.'
    else:
        try:    price = float(price)
        except: errors['price'] = 'Price must be a valid number.'
    if not address: errors['address'] = 'Address is required.'

    if errors:
        d = dict(f); d.update({'services': f.getlist('services'), 'id': pid})
        flash('Please fix the errors below.', 'error')
        return render_template('property_form.html', prop=d, errors=errors,
                               maps_key=GOOGLE_MAPS_API_KEY,
                               user_name=session.get('user_name'),
                               user_role=session.get('user_role'))

    data = (
        title, prop_type, description, status, is_shared,
        int(total_rooms), int(avail_rooms), int(bathrooms),
        price, currency, address, city, country,
        float(lat) if lat else None, float(lng) if lng else None,
        services, contact_phone, contact_email
    )

    with get_db() as conn:
        if pid is None:
            cur = conn.execute("""
                INSERT INTO properties
                    (landlord_id,title,property_type,description,status,is_shared,
                     total_rooms,available_rooms,bathrooms,price_per_month,currency,
                     address,city,country,latitude,longitude,services,contact_phone,contact_email)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (session['user_id'], *data))
            property_id = cur.lastrowid
            flash('Property listed successfully!', 'success')
        else:
            conn.execute("""
                UPDATE properties SET
                    title=?,property_type=?,description=?,status=?,is_shared=?,
                    total_rooms=?,available_rooms=?,bathrooms=?,price_per_month=?,currency=?,
                    address=?,city=?,country=?,latitude=?,longitude=?,
                    services=?,contact_phone=?,contact_email=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND landlord_id=?
            """, (*data, pid, session['user_id']))
            property_id = pid
            flash('Property updated successfully!', 'success')
        conn.commit()

    # Save any uploaded images
    uploaded = request.files.getlist('images')
    if uploaded:
        _save_images(property_id, uploaded)

    return redirect(url_for('landlord_dashboard'))


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(role_redirect(session.get('user_role')))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if 'user_id' in session:
        return redirect(role_redirect(session.get('user_role')))
    error = None
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json()
            email, password, remember = (
                data.get('email','').strip().lower(),
                data.get('password',''),
                data.get('remember', False)
            )
        else:
            email    = request.form.get('email','').strip().lower()
            password = request.form.get('password','')
            remember = bool(request.form.get('remember'))

        ip     = get_remote_address()
        failed = get_failed_attempts(email, ip)

        if   failed >= 5:              msg = "Too many failed attempts. Wait 15 minutes."
        elif not email or not password: msg = "Email and password are required."
        elif not is_valid_email(email): msg = "Please enter a valid email address."
        else:                           msg = None

        if msg:
            if request.is_json: return jsonify({'success': False, 'error': msg}), 429 if failed >= 5 else 400
            error = msg
        else:
            with get_db() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE email=? AND is_active=1", (email,)
                ).fetchone()

            if user and check_password_hash(user['password_hash'], password):
                log_attempt(email, ip, True)
                session.clear()
                session['user_id']    = user['id']
                session['user_name']  = user['full_name']
                session['user_role']  = user['role']
                session['user_email'] = user['email']
                if remember: session.permanent = True
                with get_db() as conn:
                    conn.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP, last_seen=CURRENT_TIMESTAMP WHERE id=?", (user['id'],))
                    conn.commit()
                dest = role_redirect(user['role'])
                if request.is_json:
                    return jsonify({'success': True, 'redirect': dest, 'role': user['role']})
                return redirect(dest)
            else:
                log_attempt(email, ip, False)
                msg = "Invalid email or password. Please try again."
                if request.is_json: return jsonify({'success': False, 'error': msg}), 401
                error = msg

    return render_template('login.html', error=error)


@app.route('/dashboard')
@login_required
def dashboard():
    if session.get('user_role') == 'landlord':
        return redirect(url_for('landlord_dashboard'))
    unread = get_unread_count(session['user_id'])
    return render_template('dashboard.html',
                           user_name=session.get('user_name'),
                           user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           unread_count=unread)


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.get_json() or request.form).get('email', '').strip().lower()
        msg = "If that email is registered, a reset link has been sent."
        if request.is_json: return jsonify({'success': True, 'message': msg})
        flash(msg, 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


# ── Landlord routes ───────────────────────────────────────────────────────────

@app.route('/landlord')
@landlord_required
def landlord_dashboard():
    lid = session['user_id']
    with get_db() as conn:
        props = conn.execute(
            "SELECT * FROM properties WHERE landlord_id=? AND is_active=1 ORDER BY created_at DESC", (lid,)
        ).fetchall()
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN status='available'   THEN 1 ELSE 0 END) as available,
                SUM(CASE WHEN status='occupied'    THEN 1 ELSE 0 END) as occupied,
                SUM(CASE WHEN status='partial'     THEN 1 ELSE 0 END) as partial,
                SUM(available_rooms) as total_available_rooms,
                SUM(price_per_month) as total_monthly
            FROM properties WHERE landlord_id=? AND is_active=1
        """, (lid,)).fetchone()

    # Fetch cover image for each property
    prop_list = []
    for p in props:
        d = {**dict(p), 'services': json.loads(p['services'] or '[]')}
        with get_db() as conn:
            cover = conn.execute(
                "SELECT filename FROM property_images WHERE property_id=? AND is_primary=1 LIMIT 1", (p['id'],)
            ).fetchone()
        d['cover_image'] = cover['filename'] if cover else None
        prop_list.append(d)

    unread = get_unread_count(lid)
    return render_template('landlord_dashboard.html',
                           user_name=session.get('user_name'),
                           user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           properties=prop_list, stats=stats,
                           unread_count=unread)


@app.route('/landlord/property/new', methods=['GET', 'POST'])
@landlord_required
def property_new():
    if request.method == 'POST': return _save_property(None)
    return render_template('property_form.html', prop=None, maps_key=GOOGLE_MAPS_API_KEY,
                           user_name=session.get('user_name'), user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           unread_count=get_unread_count(session['user_id']))


@app.route('/landlord/property/<int:pid>/edit', methods=['GET', 'POST'])
@landlord_required
def property_edit(pid):
    prop = _get_own_property(pid)
    if not prop:
        flash('Property not found.', 'error')
        return redirect(url_for('landlord_dashboard'))
    if request.method == 'POST': return _save_property(pid)
    d = {**dict(prop), 'services': json.loads(prop['services'] or '[]')}
    d['images'] = _get_images(pid)
    return render_template('property_form.html', prop=d, maps_key=GOOGLE_MAPS_API_KEY,
                           user_name=session.get('user_name'), user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           unread_count=get_unread_count(session['user_id']))


@app.route('/landlord/property/<int:pid>/delete', methods=['POST'])
@landlord_required
def property_delete(pid):
    if not _get_own_property(pid):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    with get_db() as conn:
        conn.execute("UPDATE properties SET is_active=0 WHERE id=?", (pid,))
        conn.commit()
    if request.is_json: return jsonify({'success': True})
    flash('Property deleted.', 'success')
    return redirect(url_for('landlord_dashboard'))


@app.route('/landlord/property/<int:pid>/image/<int:img_id>/delete', methods=['POST'])
@login_required
def property_image_delete(pid, img_id):
    uid  = session['user_id']
    role = session.get('user_role')
    with get_db() as conn:
        prop = conn.execute("SELECT landlord_id FROM properties WHERE id=?", (pid,)).fetchone()
        if not prop or (prop['landlord_id'] != uid and role != 'admin'):
            return jsonify({'error': 'Not authorized'}), 403
        img = conn.execute(
            "SELECT filename FROM property_images WHERE id=? AND property_id=?", (img_id, pid)
        ).fetchone()
        if not img:
            return jsonify({'error': 'Not found'}), 404
        conn.execute("DELETE FROM property_images WHERE id=?", (img_id,))
        conn.commit()
    path = os.path.join(UPLOAD_FOLDER, img['filename'])
    if os.path.exists(path):
        os.remove(path)
    return jsonify({'success': True})


@app.route('/landlord/property/<int:pid>/image/<int:img_id>/set-cover', methods=['POST'])
@login_required
def property_image_set_cover(pid, img_id):
    uid  = session['user_id']
    role = session.get('user_role')
    with get_db() as conn:
        prop = conn.execute("SELECT landlord_id FROM properties WHERE id=?", (pid,)).fetchone()
        if not prop or (prop['landlord_id'] != uid and role != 'admin'):
            return jsonify({'error': 'Not authorized'}), 403
        if not conn.execute(
            "SELECT id FROM property_images WHERE id=? AND property_id=?", (img_id, pid)
        ).fetchone():
            return jsonify({'error': 'Not found'}), 404
        conn.execute("UPDATE property_images SET is_primary=0 WHERE property_id=?", (pid,))
        conn.execute("UPDATE property_images SET is_primary=1 WHERE id=?", (img_id,))
        conn.commit()
    return jsonify({'success': True})


@app.route('/landlord/property/<int:pid>')
@login_required
def property_view(pid):
    with get_db() as conn:
        prop = conn.execute(
            """SELECT p.*, u.full_name as landlord_name, u.id as landlord_user_id
               FROM properties p JOIN users u ON p.landlord_id = u.id
               WHERE p.id=? AND p.is_active=1""", (pid,)
        ).fetchone()
    if not prop:
        flash('Property not found.', 'error')
        return redirect(url_for('dashboard'))
    d = {**dict(prop), 'services': json.loads(prop['services'] or '[]'),
         'images': _get_images(pid)}
    return render_template('property_view.html', prop=d, maps_key=GOOGLE_MAPS_API_KEY,
                           user_name=session.get('user_name'),
                           user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           current_user_id=session.get('user_id'),
                           unread_count=get_unread_count(session['user_id']))


# ── Messaging routes ──────────────────────────────────────────────────────────

@app.route('/messages')
@login_required
def messages_page():
    uid = session['user_id']
    open_conv = request.args.get('c', type=int)
    with get_db() as conn:
        conn.execute("UPDATE users SET last_seen=CURRENT_TIMESTAMP WHERE id=?", (uid,))
        conn.commit()
    return render_template('chat.html',
                           user_name=session.get('user_name'),
                           user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           current_user_id=uid,
                           open_conv=open_conv,
                           unread_count=0)


@app.route('/api/conversations')
@login_required
def api_conversations():
    uid = session['user_id']
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                c.id, c.subject, c.property_id, c.updated_at,
                p.title as property_title,
                u.id as other_id, u.full_name as other_name, u.role as other_role,
                u.last_seen as other_last_seen,
                (SELECT content FROM messages WHERE conversation_id=c.id
                 AND is_deleted=0 ORDER BY sent_at DESC LIMIT 1) as last_msg,
                (SELECT sent_at FROM messages WHERE conversation_id=c.id
                 AND is_deleted=0 ORDER BY sent_at DESC LIMIT 1) as last_msg_time,
                (SELECT sender_id FROM messages WHERE conversation_id=c.id
                 AND is_deleted=0 ORDER BY sent_at DESC LIMIT 1) as last_sender_id,
                (SELECT COUNT(*) FROM messages m2
                 JOIN conversation_members cm2 ON m2.conversation_id=cm2.conversation_id AND cm2.user_id=?
                 WHERE m2.conversation_id=c.id AND m2.sender_id!=?
                   AND (m2.sent_at > cm2.last_read_at OR cm2.last_read_at IS NULL)
                   AND m2.is_deleted=0) as unread
            FROM conversations c
            JOIN conversation_members cm ON c.id = cm.conversation_id AND cm.user_id = ?
            JOIN conversation_members cm2 ON c.id = cm2.conversation_id AND cm2.user_id != ?
            JOIN users u ON cm2.user_id = u.id
            LEFT JOIN properties p ON c.property_id = p.id
            ORDER BY COALESCE(last_msg_time, c.updated_at) DESC
        """, (uid, uid, uid, uid)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/messages/<int:conv_id>')
@login_required
def api_get_messages(conv_id):
    uid = session['user_id']
    with get_db() as conn:
        member = conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        ).fetchone()
        if not member:
            return jsonify({'error': 'Not a member'}), 403

        rows = conn.execute("""
            SELECT m.id, m.sender_id, m.content, m.sent_at,
                   u.full_name as sender_name, u.role as sender_role
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id=? AND m.is_deleted=0
            ORDER BY m.sent_at ASC
        """, (conv_id,)).fetchall()

        conn.execute("""
            UPDATE conversation_members SET last_read_at=CURRENT_TIMESTAMP
            WHERE conversation_id=? AND user_id=?
        """, (conv_id, uid))
        conn.commit()

    return jsonify([dict(r) for r in rows])


@app.route('/api/conversations/start', methods=['POST'])
@login_required
def api_start_conversation():
    uid  = session['user_id']
    data = request.get_json() or {}
    recipient_id = data.get('recipient_id')
    property_id  = data.get('property_id')
    subject      = data.get('subject', 'Property Inquiry')

    if not recipient_id:
        return jsonify({'error': 'recipient_id required'}), 400
    if recipient_id == uid:
        return jsonify({'error': 'Cannot message yourself'}), 400

    with get_db() as conn:
        # Find existing conversation between these two users about this property
        if property_id:
            existing = conn.execute("""
                SELECT c.id FROM conversations c
                JOIN conversation_members cm1 ON c.id=cm1.conversation_id AND cm1.user_id=?
                JOIN conversation_members cm2 ON c.id=cm2.conversation_id AND cm2.user_id=?
                WHERE c.property_id=?
                LIMIT 1
            """, (uid, recipient_id, property_id)).fetchone()
        else:
            existing = conn.execute("""
                SELECT c.id FROM conversations c
                JOIN conversation_members cm1 ON c.id=cm1.conversation_id AND cm1.user_id=?
                JOIN conversation_members cm2 ON c.id=cm2.conversation_id AND cm2.user_id=?
                LIMIT 1
            """, (uid, recipient_id)).fetchone()

        if existing:
            return jsonify({'conv_id': existing['id']})

        # Create new conversation
        cur = conn.execute(
            "INSERT INTO conversations (subject, property_id) VALUES (?,?)",
            (subject, property_id)
        )
        conv_id = cur.lastrowid
        conn.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?,?)", (conv_id, uid))
        conn.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?,?)", (conv_id, recipient_id))
        conn.commit()

    return jsonify({'conv_id': conv_id})


@app.route('/api/conversations/<int:conv_id>/read', methods=['POST'])
@login_required
def api_mark_read(conv_id):
    uid = session['user_id']
    with get_db() as conn:
        conn.execute("""
            UPDATE conversation_members SET last_read_at=CURRENT_TIMESTAMP
            WHERE conversation_id=? AND user_id=?
        """, (conv_id, uid))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/messages/unread-count')
@login_required
def api_unread_count():
    return jsonify({'count': get_unread_count(session['user_id'])})


@app.route('/api/properties')
def api_properties():
    status = request.args.get('status')
    city   = request.args.get('city')
    query  = "SELECT * FROM properties WHERE is_active=1"
    params = []
    if status: query += " AND status=?";       params.append(status)
    if city:   query += " AND city LIKE ?";    params.append(f'%{city}%')
    query += " ORDER BY created_at DESC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return jsonify([{**dict(r), 'services': json.loads(r['services'] or '[]')} for r in rows])


@app.route('/api/check-session')
def check_session():
    if 'user_id' in session:
        return jsonify({'authenticated': True, 'role': session.get('user_role')})
    return jsonify({'authenticated': False}), 401


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    if 'user_id' not in session:
        return False  # reject
    uid = session['user_id']
    with get_db() as conn:
        conn.execute("UPDATE users SET last_seen=CURRENT_TIMESTAMP WHERE id=?", (uid,))
        conn.commit()


@socketio.on('join_conv')
def on_join(data):
    if 'user_id' not in session:
        return
    uid     = session['user_id']
    conv_id = data.get('conv_id')
    with get_db() as conn:
        member = conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        ).fetchone()
    if member:
        join_room(f'conv_{conv_id}')
        emit('joined', {'conv_id': conv_id})


@socketio.on('leave_conv')
def on_leave(data):
    conv_id = data.get('conv_id')
    leave_room(f'conv_{conv_id}')


@socketio.on('send_msg')
def on_send_message(data):
    if 'user_id' not in session:
        return
    uid     = session['user_id']
    conv_id = data.get('conv_id')
    content = (data.get('content') or '').strip()

    if not content or not conv_id:
        return

    with get_db() as conn:
        member = conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        ).fetchone()
        if not member:
            return

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_id, content) VALUES (?,?,?)",
            (conv_id, uid, content)
        )
        msg_id = cur.lastrowid

        conn.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conv_id,)
        )
        conn.execute(
            "UPDATE conversation_members SET last_read_at=CURRENT_TIMESTAMP WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        )
        conn.commit()

        msg_row = conn.execute(
            "SELECT m.*, u.full_name as sender_name, u.role as sender_role FROM messages m JOIN users u ON m.sender_id=u.id WHERE m.id=?",
            (msg_id,)
        ).fetchone()

    emit('new_msg', dict(msg_row), room=f'conv_{conv_id}')


@socketio.on('typing')
def on_typing(data):
    if 'user_id' not in session:
        return
    conv_id = data.get('conv_id')
    emit('typing_update', {
        'user_id':   session['user_id'],
        'user_name': session.get('user_name'),
        'typing':    data.get('typing', False),
    }, room=f'conv_{conv_id}', include_self=False)


if __name__ == '__main__':
    init_db()
    # Use Werkzeug's standard dev server so all HTTP routes work correctly.
    # Socket.IO automatically falls back to long-polling, which is fine for development.
    app.run(debug=True, port=5000)
