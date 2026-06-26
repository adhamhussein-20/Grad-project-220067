"""Auth decorators for route protection — login, role, CSRF."""
from functools import wraps
from flask import session, redirect, url_for, flash, request
import secrets


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*a, **kw)
    return wrap


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrap(*a, **kw):
            if session.get('role') not in roles:
                flash('Access denied', 'error')
                return redirect(url_for('dashboard'))
            return f(*a, **kw)
        return wrap
    return decorator


def csrf_required(f):
    """Validate CSRF token on POST/PUT/DELETE requests."""
    @wraps(f)
    def wrap(*a, **kw):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            token = request.form.get('csrf_token', '')
            expected = session.get('csrf_token', '')
            if not token or not expected or not secrets.compare_digest(token, expected):
                flash('Invalid form submission. Please try again.', 'error')
                return redirect(request.referrer or url_for('dashboard'))
        return f(*a, **kw)
    return wrap
