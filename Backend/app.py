from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, make_response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
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

GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')

oauth = OAuth(app)
google_oauth = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

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
            ("google_id", "TEXT"),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")

        prop_cols = {r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()}
        if 'nearby_landmark' not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN nearby_landmark TEXT DEFAULT ''")
        if 'student_friendly' not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN student_friendly INTEGER DEFAULT 0")
        conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                phone TEXT,
                google_id TEXT,
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

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                property_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                reference TEXT,
                paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES users(id),
                FOREIGN KEY (property_id) REFERENCES properties(id),
                UNIQUE(student_id, property_id)
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


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('user_role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_valid_email(email):
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email)


def is_valid_phone(phone):
    return re.match(r'^\+?[\d\s\-]{7,15}$', phone)


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
        'admin':    url_for('admin_dashboard'),
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


def has_paid(student_id, property_id):
    with get_db() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM payments WHERE student_id=? AND property_id=?",
            (student_id, property_id)
        ).fetchone())


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
    services         = json.dumps(f.getlist('services'))
    contact_phone    = f.get('contact_phone', '').strip()
    contact_email    = f.get('contact_email', '').strip()
    nearby_landmark  = f.get('nearby_landmark', '').strip()
    student_friendly = 1 if f.get('student_friendly') else 0

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
        services, contact_phone, contact_email, nearby_landmark, student_friendly
    )

    with get_db() as conn:
        if pid is None:
            cur = conn.execute("""
                INSERT INTO properties
                    (landlord_id,title,property_type,description,status,is_shared,
                     total_rooms,available_rooms,bathrooms,price_per_month,currency,
                     address,city,country,latitude,longitude,services,contact_phone,contact_email,
                     nearby_landmark,student_friendly)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (session['user_id'], *data))
            property_id = cur.lastrowid
            flash('Property listed successfully!', 'success')
        else:
            conn.execute("""
                UPDATE properties SET
                    title=?,property_type=?,description=?,status=?,is_shared=?,
                    total_rooms=?,available_rooms=?,bathrooms=?,price_per_month=?,currency=?,
                    address=?,city=?,country=?,latitude=?,longitude=?,
                    services=?,contact_phone=?,contact_email=?,nearby_landmark=?,student_friendly=?,
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


@app.route('/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    if 'user_id' in session:
        return jsonify({'success': False, 'error': 'Already logged in'}), 400

    data      = request.get_json() if request.is_json else request.form
    full_name = (data.get('full_name') or '').strip()
    email     = (data.get('email') or '').strip().lower()
    phone     = (data.get('phone') or '').strip()
    password  = data.get('password') or ''
    role      = (data.get('role') or '').strip()

    def err(msg, code=400):
        return jsonify({'success': False, 'error': msg}), code

    if not full_name:
        return err('Full name is required.')
    if not email or not is_valid_email(email):
        return err('A valid email address is required.')
    if phone and not is_valid_phone(phone):
        return err('Please enter a valid phone number.')
    if role not in ('student', 'landlord'):
        return err('Please select Tenant or Landlord.')
    if len(password) < 8:
        return err('Password must be at least 8 characters.')

    with get_db() as conn:
        if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return err('An account with this email already exists.')
        if phone and conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone():
            return err('An account with this phone number already exists.')
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash, role, phone) VALUES (?,?,?,?,?)",
            (full_name, email, generate_password_hash(password), role, phone or None)
        )
        conn.commit()
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']

    session.clear()
    session['user_id']    = user_id
    session['user_name']  = full_name
    session['user_role']  = role
    session['user_email'] = email

    dest = role_redirect(role)
    if request.is_json:
        return jsonify({'success': True, 'redirect': dest, 'role': role})
    return redirect(dest)


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if 'user_id' in session:
        return redirect(role_redirect(session.get('user_role')))
    error = None
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json()
            identifier = (data.get('email') or data.get('identifier') or '').strip()
            password   = data.get('password', '')
            remember   = data.get('remember', False)
        else:
            identifier = request.form.get('email', '').strip()
            password   = request.form.get('password', '')
            remember   = bool(request.form.get('remember'))

        identifier_lower = identifier.lower()
        ip     = get_remote_address()
        failed = get_failed_attempts(identifier_lower, ip)

        login_by_phone = '@' not in identifier and is_valid_phone(identifier)
        if   failed >= 5:                    msg = "Too many failed attempts. Wait 15 minutes."
        elif not identifier or not password: msg = "Email/phone and password are required."
        elif not login_by_phone and not is_valid_email(identifier_lower): msg = "Please enter a valid email address or phone number."
        else:                                msg = None

        if msg:
            if request.is_json: return jsonify({'success': False, 'error': msg}), 429 if failed >= 5 else 400
            error = msg
        else:
            with get_db() as conn:
                if login_by_phone:
                    user = conn.execute(
                        "SELECT * FROM users WHERE phone=? AND is_active=1", (identifier,)
                    ).fetchone()
                else:
                    user = conn.execute(
                        "SELECT * FROM users WHERE email=? AND is_active=1", (identifier_lower,)
                    ).fetchone()

            if user and user['password_hash'] and check_password_hash(user['password_hash'], password):
                log_attempt(identifier_lower, ip, True)
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
                log_attempt(identifier_lower, ip, False)
                msg = "Invalid credentials. Please try again."
                if request.is_json: return jsonify({'success': False, 'error': msg}), 401
                error = msg

    return render_template('login.html', error=error)


@app.route('/auth/google')
def auth_google():
    if not GOOGLE_CLIENT_ID:
        flash('Google login is not configured yet.', 'error')
        return redirect(url_for('login'))
    redirect_uri = url_for('auth_google_callback', _external=True)
    return google_oauth.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def auth_google_callback():
    if not GOOGLE_CLIENT_ID:
        return redirect(url_for('login'))
    try:
        token     = google_oauth.authorize_access_token()
        user_info = token.get('userinfo') or {}
    except Exception:
        flash('Google login failed. Please try again.', 'error')
        return redirect(url_for('login'))

    g_email = (user_info.get('email') or '').lower()
    g_name  = user_info.get('name') or g_email.split('@')[0]
    g_id    = user_info.get('sub') or ''

    if not g_email:
        flash('Could not retrieve your email from Google.', 'error')
        return redirect(url_for('login'))

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE google_id=? OR email=?", (g_id, g_email)
        ).fetchone()

        if user:
            if not user['google_id']:
                conn.execute("UPDATE users SET google_id=? WHERE id=?", (g_id, user['id']))
                conn.commit()
            conn.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP, last_seen=CURRENT_TIMESTAMP WHERE id=?", (user['id'],))
            conn.commit()
            session.clear()
            session['user_id']    = user['id']
            session['user_name']  = user['full_name']
            session['user_role']  = user['role']
            session['user_email'] = user['email']
            return redirect(role_redirect(user['role']))

    # New Google user — store info temporarily and ask for role
    session['pending_google'] = {'email': g_email, 'name': g_name, 'google_id': g_id}
    return redirect(url_for('choose_role'))


@app.route('/auth/choose-role', methods=['GET', 'POST'])
def choose_role():
    pending = session.get('pending_google')
    if not pending:
        return redirect(url_for('login'))

    if request.method == 'POST':
        role = request.form.get('role', '').strip()
        if role not in ('student', 'landlord'):
            return render_template('choose_role.html', error='Please select your account type.')

        g_email = pending['email']
        g_name  = pending['name']
        g_id    = pending['google_id']

        with get_db() as conn:
            if conn.execute("SELECT id FROM users WHERE email=?", (g_email,)).fetchone():
                flash('An account with this email already exists. Please sign in instead.', 'error')
                session.pop('pending_google', None)
                return redirect(url_for('login'))
            conn.execute(
                "INSERT INTO users (full_name, email, password_hash, role, google_id) VALUES (?,?,?,?,?)",
                (g_name, g_email, '', role, g_id)
            )
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE email=?", (g_email,)).fetchone()

        session.pop('pending_google', None)
        session.clear()
        session['user_id']    = user['id']
        session['user_name']  = user['full_name']
        session['user_role']  = user['role']
        session['user_email'] = user['email']
        return redirect(role_redirect(role))

    return render_template('choose_role.html', name=pending.get('name', ''), error=None)


@app.route('/dashboard')
@login_required
def dashboard():
    if session.get('user_role') == 'landlord':
        return redirect(url_for('landlord_dashboard'))
    if session.get('user_role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    q               = request.args.get('q', '').strip()
    prop_type       = request.args.get('type', '').strip()
    city            = request.args.get('city', '').strip()
    min_price       = request.args.get('min_price', '').strip()
    max_price       = request.args.get('max_price', '').strip()
    shared          = request.args.get('shared', '').strip()
    student_friendly= request.args.get('student_friendly', '').strip()
    available_only  = request.args.get('available_only', '').strip()

    filters = ["p.is_active=1"]
    params  = []
    if q:
        like = f'%{q}%'
        filters.append("(p.title LIKE ? OR p.nearby_landmark LIKE ? OR p.city LIKE ? OR p.description LIKE ?)")
        params += [like, like, like, like]
    if prop_type:
        filters.append("p.property_type=?");  params.append(prop_type)
    if city:
        filters.append("p.city=?");           params.append(city)
    if min_price:
        try:    filters.append("p.price_per_month>=?"); params.append(float(min_price))
        except ValueError: pass
    if max_price:
        try:    filters.append("p.price_per_month<=?"); params.append(float(max_price))
        except ValueError: pass
    if shared == '1':   filters.append("p.is_shared=1")
    elif shared == '0': filters.append("p.is_shared=0")
    if student_friendly == '1': filters.append("p.student_friendly=1")
    if available_only   == '1': filters.append("p.status='available'")

    where = ' AND '.join(filters)
    with get_db() as conn:
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) as available,
                   SUM(CASE WHEN available_rooms > 0 THEN available_rooms ELSE 0 END) as rooms_available
            FROM properties WHERE is_active=1
        """).fetchone()

        props = conn.execute(f"""
            SELECT p.*, u.full_name as landlord_name
            FROM properties p JOIN users u ON p.landlord_id=u.id
            WHERE {where}
            ORDER BY p.created_at DESC
        """, params).fetchall()

    prop_list = []
    for p in props:
        d = {**dict(p), 'services': json.loads(p['services'] or '[]')}
        with get_db() as conn:
            cover = conn.execute(
                "SELECT filename FROM property_images WHERE property_id=? AND is_primary=1 LIMIT 1",
                (p['id'],)
            ).fetchone()
        d['cover_image'] = cover['filename'] if cover else None
        prop_list.append(d)

    unread = get_unread_count(session['user_id'])
    return render_template('dashboard.html',
                           user_name=session.get('user_name'),
                           user_role=session.get('user_role'),
                           user_email=session.get('user_email'),
                           properties=prop_list,
                           stats=stats,
                           cities=_get_cities(),
                           q=q, prop_type=prop_type, city=city,
                           min_price=min_price, max_price=max_price,
                           shared=shared, student_friendly=student_friendly,
                           available_only=available_only,
                           unread_count=unread)


def _get_cities():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT city, COUNT(*) as cnt
            FROM properties
            WHERE is_active=1 AND city IS NOT NULL AND city != ''
            GROUP BY city ORDER BY cnt DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.route('/browse')
def browse():
    q         = request.args.get('q', '').strip()
    prop_type = request.args.get('type', '').strip()
    max_price = request.args.get('max_price', '').strip()
    city      = request.args.get('city', '').strip()

    filters = ["p.is_active=1"]
    params  = []
    if q:
        like = f'%{q}%'
        filters.append("(p.title LIKE ? OR p.nearby_landmark LIKE ? OR p.city LIKE ? OR p.description LIKE ?)")
        params += [like, like, like, like]
    if prop_type:
        filters.append("p.property_type=?")
        params.append(prop_type)
    if max_price:
        try:
            filters.append("p.price_per_month<=?")
            params.append(float(max_price))
        except ValueError:
            pass
    if city:
        filters.append("p.city=?")
        params.append(city)

    where = ' AND '.join(filters)
    with get_db() as conn:
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) as available,
                   SUM(CASE WHEN available_rooms > 0 THEN available_rooms ELSE 0 END) as rooms_available
            FROM properties WHERE is_active=1
        """).fetchone()

        props = conn.execute(f"""
            SELECT p.*, u.full_name as landlord_name
            FROM properties p JOIN users u ON p.landlord_id=u.id
            WHERE {where}
            ORDER BY p.created_at DESC
        """, params).fetchall()

    prop_list = []
    for p in props:
        d = {**dict(p), 'services': json.loads(p['services'] or '[]')}
        with get_db() as conn:
            cover = conn.execute(
                "SELECT filename FROM property_images WHERE property_id=? AND is_primary=1 LIMIT 1",
                (p['id'],)
            ).fetchone()
        d['cover_image'] = cover['filename'] if cover else None
        prop_list.append(d)

    return render_template('browse.html',
                           properties=prop_list,
                           stats=stats,
                           cities=_get_cities(),
                           q=q, prop_type=prop_type, max_price=max_price, city=city)


@app.route('/for-tenants')
def for_tenants():
    return redirect(url_for('browse'))


@app.route('/join')
def join():
    return redirect('/login#register')


@app.route('/manifest.json')
def pwa_manifest():
    return jsonify({
        "name": "T-Tech Connect",
        "short_name": "T-Tech",
        "description": "Connecting Tenants with Landlords",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#1d4ed8",
        "orientation": "portrait-primary",
        "categories": ["real estate", "housing"],
        "icons": [
            {"src": "/static/images/icon-72.png",  "sizes": "72x72",   "type": "image/png"},
            {"src": "/static/images/icon-96.png",  "sizes": "96x96",   "type": "image/png"},
            {"src": "/static/images/icon-128.png", "sizes": "128x128", "type": "image/png"},
            {"src": "/static/images/icon-144.png", "sizes": "144x144", "type": "image/png"},
            {"src": "/static/images/icon-152.png", "sizes": "152x152", "type": "image/png"},
            {"src": "/static/images/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/images/icon-384.png", "sizes": "384x384", "type": "image/png"},
            {"src": "/static/images/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ]
    })


@app.route('/sw.js')
def service_worker():
    resp = make_response(
        open(os.path.join(app.root_path, '..', 'Frontend', 'static', 'js', 'sw.js')).read()
    )
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/offline')
def offline_page():
    return render_template('offline.html')


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


@app.route('/property/<int:pid>/pay', methods=['POST'])
@login_required
def pay_commission(pid):
    uid  = session['user_id']
    role = session.get('user_role')
    if role not in ('student', 'admin'):
        return jsonify({'error': 'Only students can pay commission'}), 403

    with get_db() as conn:
        prop = conn.execute(
            "SELECT price_per_month, currency FROM properties WHERE id=? AND is_active=1", (pid,)
        ).fetchone()
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    if has_paid(uid, pid):
        return jsonify({'success': True, 'already_paid': True})

    data      = request.get_json() or {}
    reference = data.get('reference', '').strip()
    method    = data.get('method', '').strip()
    if not reference:
        return jsonify({'error': 'Payment reference is required'}), 400

    amount = round(prop['price_per_month'] * 0.05, 2)
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payments (student_id, property_id, amount, currency, reference)
               VALUES (?,?,?,?,?)""",
            (uid, pid, amount, prop['currency'], f"[{method}] {reference}")
        )
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
    d['commission'] = round(d['price_per_month'] * 0.05, 2)

    uid  = session['user_id']
    role = session.get('user_role')
    # Landlords, admins, and the property owner always have full access
    paid = True if role in ('landlord', 'admin') else has_paid(uid, pid)

    return render_template('property_view.html', prop=d, maps_key=GOOGLE_MAPS_API_KEY,
                           user_name=session.get('user_name'),
                           user_role=role,
                           user_email=session.get('user_email'),
                           current_user_id=uid,
                           has_paid=paid,
                           unread_count=get_unread_count(uid))


# ── Messaging routes ──────────────────────────────────────────────────────────

@app.route('/contact-support')
@login_required
def contact_support():
    uid  = session['user_id']
    role = session.get('user_role')
    if role == 'admin':
        return redirect(url_for('messages_page'))

    with get_db() as conn:
        admin = conn.execute(
            "SELECT id FROM users WHERE role='admin' AND is_active=1 AND id!=? ORDER BY id LIMIT 1",
            (uid,)
        ).fetchone()
        if not admin:
            flash('No support agent is available right now. Please try again later.', 'warning')
            return redirect(url_for('dashboard') if role == 'student' else url_for('landlord_dashboard'))

        admin_id = admin['id']

        # Find existing support conversation (no property attached)
        existing = conn.execute("""
            SELECT c.id FROM conversations c
            JOIN conversation_members cm1 ON c.id=cm1.conversation_id AND cm1.user_id=?
            JOIN conversation_members cm2 ON c.id=cm2.conversation_id AND cm2.user_id=?
            WHERE c.property_id IS NULL
            LIMIT 1
        """, (uid, admin_id)).fetchone()

        if existing:
            return redirect(url_for('messages_page', c=existing['id']))

        cur = conn.execute(
            "INSERT INTO conversations (subject, property_id) VALUES (?, NULL)",
            ('T-Tech Connect Support',)
        )
        conv_id = cur.lastrowid
        conn.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?,?)", (conv_id, uid))
        conn.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?,?)", (conv_id, admin_id))
        conn.commit()

    return redirect(url_for('messages_page', c=conv_id))


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

    # Commission gate: students need to have paid for the property — unless messaging admin
    role = session.get('user_role')
    if role == 'student' and property_id:
        with get_db() as conn:
            recipient_role = (conn.execute(
                "SELECT role FROM users WHERE id=?", (recipient_id,)
            ).fetchone() or {}).get('role')
        if recipient_role != 'admin' and not has_paid(uid, property_id):
            return jsonify({'error': 'Commission payment required to contact this landlord'}), 403

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


@app.route('/api/conversations/<int:conv_id>/send', methods=['POST'])
@login_required
def api_send_message_rest(conv_id):
    uid     = session['user_id']
    data    = request.get_json() or {}
    content = (data.get('content') or '').strip()

    if not content:
        return jsonify({'error': 'Message cannot be empty'}), 400

    with get_db() as conn:
        if not conn.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        ).fetchone():
            return jsonify({'error': 'Not a member'}), 403

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_id, content) VALUES (?,?,?)",
            (conv_id, uid, content)
        )
        msg_id = cur.lastrowid
        conn.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conv_id,))
        conn.execute(
            "UPDATE conversation_members SET last_read_at=CURRENT_TIMESTAMP WHERE conversation_id=? AND user_id=?",
            (conv_id, uid)
        )
        conn.commit()

        msg_row = conn.execute(
            """SELECT m.*, u.full_name as sender_name, u.role as sender_role
               FROM messages m JOIN users u ON m.sender_id=u.id WHERE m.id=?""",
            (msg_id,)
        ).fetchone()

    # Push to anyone already in the Socket.IO room (e.g. landlord online in chat)
    socketio.emit('new_msg', dict(msg_row), room=f'conv_{conv_id}')
    return jsonify({'success': True, 'msg_id': msg_id})


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


@app.route('/api/users/search')
@login_required
def api_users_search():
    uid  = session['user_id']
    role = session.get('user_role')
    q    = request.args.get('q', '').strip()
    with get_db() as conn:
        if role == 'admin':
            # Admin can search all active users except themselves
            if q:
                rows = conn.execute(
                    """SELECT id, full_name, email, role FROM users
                       WHERE is_active=1 AND id!=?
                         AND (full_name LIKE ? OR email LIKE ?)
                       ORDER BY full_name LIMIT 20""",
                    (uid, f'%{q}%', f'%{q}%')
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, full_name, email, role FROM users WHERE is_active=1 AND id!=? ORDER BY full_name LIMIT 20",
                    (uid,)
                ).fetchall()
        else:
            return jsonify({'error': 'Not authorised'}), 403
    return jsonify([dict(r) for r in rows])


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


# ── Admin routes ──────────────────────────────────────────────────────────────

def _admin_common():
    return dict(user_name=session.get('user_name'), user_role=session.get('user_role'),
                unread_count=get_unread_count(session['user_id']))


@app.route('/admin')
@admin_required
def admin_dashboard():
    with get_db() as conn:
        stats = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM users WHERE is_active=1)                           AS total_users,
              (SELECT COUNT(*) FROM users WHERE role='student'  AND is_active=1)       AS students,
              (SELECT COUNT(*) FROM users WHERE role='landlord' AND is_active=1)       AS landlords,
              (SELECT COUNT(*) FROM properties WHERE is_active=1)                      AS total_props,
              (SELECT COUNT(*) FROM properties WHERE status='available' AND is_active=1) AS avail_props,
              (SELECT COALESCE(SUM(amount),0) FROM payments)                           AS total_revenue,
              (SELECT COUNT(*) FROM payments)                                          AS total_payments
        """).fetchone()

        recent_users = conn.execute(
            "SELECT id,full_name,email,role,is_active,created_at FROM users ORDER BY created_at DESC LIMIT 6"
        ).fetchall()

        recent_props = conn.execute("""
            SELECT p.id,p.title,p.status,p.price_per_month,p.currency,p.created_at,
                   u.full_name AS landlord_name
            FROM properties p JOIN users u ON p.landlord_id=u.id
            WHERE p.is_active=1 ORDER BY p.created_at DESC LIMIT 6
        """).fetchall()

        recent_payments = conn.execute("""
            SELECT pay.amount,pay.currency,pay.reference,pay.paid_at,
                   u.full_name AS student_name, p.title AS property_title
            FROM payments pay
            JOIN users u ON pay.student_id=u.id
            JOIN properties p ON pay.property_id=p.id
            ORDER BY pay.paid_at DESC LIMIT 6
        """).fetchall()

    return render_template('admin_dashboard.html',
                           stats=stats,
                           recent_users=recent_users,
                           recent_props=recent_props,
                           recent_payments=recent_payments,
                           **_admin_common())


@app.route('/admin/users')
@admin_required
def admin_users():
    q           = request.args.get('q', '').strip()
    role_filter = request.args.get('role', '').strip()
    filters, params = [], []
    if q:
        filters.append("(full_name LIKE ? OR email LIKE ?)")
        params += [f'%{q}%', f'%{q}%']
    if role_filter:
        filters.append("role=?")
        params.append(role_filter)
    where = ('WHERE ' + ' AND '.join(filters)) if filters else ''
    with get_db() as conn:
        users = conn.execute(
            f"SELECT id,full_name,email,role,is_active,phone,created_at,last_login "
            f"FROM users {where} ORDER BY created_at DESC", params
        ).fetchall()
    return render_template('admin_users.html', users=users,
                           q=q, role_filter=role_filter, **_admin_common())


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@admin_required
def admin_user_toggle(uid):
    if uid == session['user_id']:
        return jsonify({'error': "You cannot deactivate your own account"}), 400
    with get_db() as conn:
        user = conn.execute("SELECT is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        new = 0 if user['is_active'] else 1
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (new, uid))
        conn.commit()
    return jsonify({'success': True, 'is_active': new})


@app.route('/admin/users/<int:uid>/set-role', methods=['POST'])
@admin_required
def admin_user_set_role(uid):
    if uid == session['user_id']:
        return jsonify({'error': "You cannot change your own role"}), 400
    role = (request.get_json() or {}).get('role', '')
    if role not in ('student', 'landlord', 'admin'):
        return jsonify({'error': 'Invalid role'}), 400
    with get_db() as conn:
        if not conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            return jsonify({'error': 'User not found'}), 404
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        conn.commit()
    return jsonify({'success': True})


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_user_delete(uid):
    if uid == session['user_id']:
        return jsonify({'error': "You cannot delete your own account"}), 400
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    return jsonify({'success': True})


@app.route('/admin/properties')
@admin_required
def admin_properties():
    q             = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()
    filters = ["p.is_active=1"]
    params  = []
    if q:
        filters.append("(p.title LIKE ? OR p.address LIKE ? OR u.full_name LIKE ?)")
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    if status_filter:
        filters.append("p.status=?")
        params.append(status_filter)
    with get_db() as conn:
        props = conn.execute(
            f"SELECT p.*,u.full_name AS landlord_name FROM properties p "
            f"JOIN users u ON p.landlord_id=u.id WHERE {' AND '.join(filters)} "
            f"ORDER BY p.created_at DESC", params
        ).fetchall()
    prop_list = [{**dict(p), 'services': json.loads(p['services'] or '[]')} for p in props]
    return render_template('admin_properties.html', properties=prop_list,
                           q=q, status_filter=status_filter, **_admin_common())


@app.route('/admin/property/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_property_delete(pid):
    with get_db() as conn:
        conn.execute("UPDATE properties SET is_active=0 WHERE id=?", (pid,))
        conn.commit()
    if request.is_json:
        return jsonify({'success': True})
    flash('Property removed.', 'success')
    return redirect(url_for('admin_properties'))


@app.route('/admin/payments')
@admin_required
def admin_payments():
    with get_db() as conn:
        payments = conn.execute("""
            SELECT pay.id,pay.amount,pay.currency,pay.reference,pay.paid_at,
                   u.full_name AS student_name, u.email AS student_email,
                   p.title AS property_title, p.id AS property_id,
                   lu.full_name AS landlord_name
            FROM payments pay
            JOIN users u  ON pay.student_id=u.id
            JOIN properties p ON pay.property_id=p.id
            JOIN users lu ON p.landlord_id=lu.id
            ORDER BY pay.paid_at DESC
        """).fetchall()
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM payments"
        ).fetchone()['t']
    return render_template('admin_payments.html', payments=payments,
                           total_revenue=total_revenue, **_admin_common())


if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
