"""Auth routes — Login, Register, Logout, Welcome with CSRF + rate limiting."""
import re, secrets
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, check_login_lockout, record_login_failure, reset_login_attempts
from config import (
    PASSWORD_MIN_LENGTH, PASSWORD_REQUIRE_UPPER,
    PASSWORD_REQUIRE_DIGIT, PASSWORD_REQUIRE_SPECIAL
)

auth_bp = Blueprint('auth', __name__)


# ── CSRF helpers ──
def _generate_csrf():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def _validate_csrf():
    """Validate CSRF token on POST requests. Returns True if valid."""
    if request.method != 'POST':
        return True
    token = request.form.get('csrf_token', '')
    expected = session.get('csrf_token', '')
    if not token or not expected or not secrets.compare_digest(token, expected):
        flash('Invalid form submission. Please try again.', 'error')
        return False
    return True


# ── Password validation ──
def _validate_password(password):
    """Returns list of password requirement failures."""
    errors = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f'Password must be at least {PASSWORD_MIN_LENGTH} characters')
    if PASSWORD_REQUIRE_UPPER and not re.search(r'[A-Z]', password):
        errors.append('Password must contain at least one uppercase letter')
    if PASSWORD_REQUIRE_DIGIT and not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one digit')
    if PASSWORD_REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append('Password must contain at least one special character')
    return errors


@auth_bp.route('/')
def index():
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # Always ensure CSRF token exists for the form
    _generate_csrf()
    if request.method == 'POST':
        if not _validate_csrf():
            return render_template('login.html')

        nat_id = request.form.get('national_id', '').strip()
        password = request.form.get('password', '')

        # ── Check lockout ──
        locked, lock_msg = check_login_lockout(nat_id)
        if locked:
            flash(lock_msg, 'error')
            return render_template('login.html')

        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE national_id=?", (nat_id,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            if user['status'] == 'pending':
                flash('Your account is pending admin approval.', 'error')
                return render_template('login.html')
            if user['status'] == 'rejected':
                flash('Your account has been rejected. Contact an admin.', 'error')
                return render_template('login.html')

            # Successful login
            reset_login_attempts(nat_id)
            actual_role = user['role']
            full_name = user['first_name'] + ' ' + user['second_name']
            session.update(
                user_id=user['id'], role=actual_role,
                name=full_name, hospital_id=user['hospital_id']
            )
            _generate_csrf()

            role_greetings = {
                'admin': 'Welcome Admin - ' + full_name,
                'doctor': 'Welcome Doctor - ' + full_name,
                'paramedic': 'Welcome Paramedic - ' + full_name,
            }
            flash(role_greetings.get(actual_role, 'Welcome, ' + user['first_name'] + '!'), 'success')
            return redirect(url_for('auth.welcome'))

        # Failed login (outside get_db context - no nested connections)
        if user:
            record_login_failure(nat_id)

        flash('Invalid national ID or password', 'error')
        return render_template('login.html')

    return render_template('login.html')


@auth_bp.route('/welcome')
def welcome():
    """Post-login welcome page before redirecting to dashboard."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('welcome.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    _generate_csrf()
    if request.method == 'POST':
        if not _validate_csrf():
            return render_template('register.html')

        nat_id = request.form.get('national_id', '').strip()
        username = request.form.get('username', '').strip()
        first = request.form.get('first_name', '').strip()
        second = request.form.get('second_name', '').strip()
        try:
            age = int(request.form.get('age', 30))
        except (ValueError, TypeError):
            age = 30
        phone = request.form.get('phone', '').strip()
        pwd1 = request.form.get('password', '')
        pwd2 = request.form.get('password2', '')

        errors = []
        if not all([nat_id, username, first, second, phone, pwd1, pwd2]):
            errors.append('All fields are required')
        if pwd1 != pwd2:
            errors.append('Passwords do not match')
        if len(nat_id) != 14 or not nat_id.isdigit():
            errors.append('Egyptian national ID must be exactly 14 digits')
        if len(phone) < 10 or not phone.isdigit():
            errors.append('Phone number must be at least 10 digits')

        # Strong password validation
        errors.extend(_validate_password(pwd1))

        with get_db() as db:
            if db.execute("SELECT id FROM users WHERE national_id=?", (nat_id,)).fetchone():
                errors.append('National ID already registered')
            if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                errors.append('Username already taken')

            if not errors:
                db.execute(
                    """INSERT INTO users
                    (national_id, username, first_name, second_name, age, phone, password_hash, role, status)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (nat_id, username, first, second, age, phone,
                     generate_password_hash(pwd1), 'patient', 'pending'))
                flash('Registration submitted! Awaiting admin approval.', 'success')
                return redirect(url_for('auth.login'))

        for e in errors:
            flash(e, 'error')
        return render_template('register.html')

    return render_template('register.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
