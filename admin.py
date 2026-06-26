"""Admin routes — Dashboard, user/hospital/injury management, analytics, exports."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response
from werkzeug.security import generate_password_hash
from decorators import login_required, role_required, csrf_required
from database import get_db
from config import BASE_URL, PASSWORD_MIN_LENGTH
from routes.auth import _validate_password
import csv, io, datetime as _dt

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ── Dashboard ──────────────────────────────────────────────────
@admin_bp.route('/')
@login_required
@role_required('admin')
def dashboard():
    with get_db() as db:
        hospitals = db.execute("SELECT * FROM hospitals ORDER BY name").fetchall()
        doctors = db.execute(
            "SELECT u.*, h.name as hospital_name FROM users u LEFT JOIN hospitals h ON u.hospital_id = h.id WHERE u.role='doctor' ORDER BY u.created_at DESC"
        ).fetchall()
        paramedics = db.execute(
            "SELECT u.*, h.name as hospital_name FROM users u LEFT JOIN hospitals h ON u.hospital_id = h.id WHERE u.role='paramedic' ORDER BY u.created_at DESC"
        ).fetchall()
        patients = db.execute(
            "SELECT * FROM users WHERE role='patient' ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        pending = db.execute(
            "SELECT * FROM users WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
        all_users = db.execute(
            """SELECT u.*, h.name as hospital_name FROM users u
            LEFT JOIN hospitals h ON u.hospital_id = h.id
            ORDER BY u.role, u.first_name"""
        ).fetchall()

        stats = {
            'hospitals': len(hospitals),
            'doctors': len(doctors),
            'paramedics': len(paramedics),
            'patients': db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient'").fetchone()['c'],
            'injuries': db.execute("SELECT COUNT(*) as c FROM injury_records").fetchone()['c'],
            'scans': db.execute("SELECT COUNT(*) as c FROM qr_scans").fetchone()['c'],
            'pending': len(pending),
        }
        stats['severity_counts'] = dict(
            db.execute('SELECT severity, COUNT(*) as c FROM injury_records GROUP BY severity').fetchall()
        )

        # ── Scan chart data (last 7 days) ──
        _today = _dt.date.today()
        _raw_scans = dict(
            db.execute(
                "SELECT date(scanned_at) as day, COUNT(*) as c FROM qr_scans WHERE scanned_at >= date('now','-6 days') GROUP BY day"
            ).fetchall()
        )
        stats['scans_per_day'] = {
            (_today - _dt.timedelta(days=i)).isoformat(): _raw_scans.get(
                (_today - _dt.timedelta(days=i)).isoformat(), 0
            )
            for i in range(6, -1, -1)
        }

    return render_template(
        'admin_dashboard.html', hospitals=hospitals, doctors=doctors,
        paramedics=paramedics, patients=patients, pending=pending,
        stats=stats, all_users=all_users
    )


@admin_bp.route('/stats')
@login_required
@role_required('admin')
def stats_api():
    with get_db() as db:
        return jsonify({
            'patients': db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient'").fetchone()['c'],
            'injuries': db.execute("SELECT COUNT(*) as c FROM injury_records").fetchone()['c'],
            'scans': db.execute("SELECT COUNT(*) as c FROM qr_scans").fetchone()['c'],
        })


# ── Hospital CRUD ──────────────────────────────────────────────
@admin_bp.route('/hospital/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def add_hospital():
    name = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    if name and location:
        with get_db() as db:
            try:
                db.execute("INSERT INTO hospitals (name, location) VALUES (?,?)", (name, location))
                flash('Hospital added', 'success')
            except Exception:
                flash('Hospital already exists or invalid data', 'error')
    else:
        flash('Name and location are required', 'error')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/hospitals')
@login_required
@role_required('admin')
def hospitals():
    with get_db() as db:
        hospitals = db.execute(
            """SELECT h.*,
            (SELECT COUNT(*) FROM users WHERE hospital_id=h.id AND role='doctor') as doctor_count,
            (SELECT COUNT(*) FROM users WHERE hospital_id=h.id AND role='paramedic') as paramedic_count
            FROM hospitals h ORDER BY h.name"""
        ).fetchall()
    return render_template('admin_hospitals.html', hospitals=hospitals)


@admin_bp.route('/hospital/edit/<int:hid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def edit_hospital(hid):
    name = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    if not name or not location:
        flash('Name and location are required', 'error')
        return redirect(url_for('admin.hospitals'))
    with get_db() as db:
        db.execute(
            "UPDATE hospitals SET name=?, location=? WHERE id=?",
            (name, location, hid)
        )
        flash('Hospital updated', 'success')
    return redirect(url_for('admin.hospitals'))


@admin_bp.route('/hospital/delete/<int:hid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def delete_hospital(hid):
    with get_db() as db:
        db.execute("UPDATE users SET hospital_id=NULL WHERE hospital_id=?", (hid,))
        db.execute("DELETE FROM hospitals WHERE id=?", (hid,))
        flash('Hospital deleted', 'success')
    return redirect(url_for('admin.hospitals'))


# ── Unified User CRUD (replaces add_doctor / add_paramedic / add_patient) ──
def _add_user_core(role, extra_fields=None):
    """Shared user creation logic. Redirects to admin.dashboard on completion."""
    nat_id = request.form.get('national_id', '').strip()
    username = request.form.get('username', '').strip()
    first = request.form.get('first_name', '').strip()
    second = request.form.get('second_name', '').strip()
    phone = request.form.get('phone', '').strip()
    pwd = request.form.get('password', '')
    age = int(request.form.get('age', 30))
    hospital_id = request.form.get('hospital_id') or None

    errors = []
    if not all([nat_id, username, first, second, phone, pwd]):
        errors.append('All fields are required')
    if len(nat_id) != 14 or not nat_id.isdigit():
        errors.append('National ID must be exactly 14 digits')
    if len(pwd) < PASSWORD_MIN_LENGTH:
        errors.append(f'Password must be at least {PASSWORD_MIN_LENGTH} characters')
    errors.extend(_validate_password(pwd))  # full policy check

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin.dashboard'))

    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO users
                (national_id, username, first_name, second_name, age, phone, password_hash, role, hospital_id, registered_by, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (nat_id, username, first, second, age, phone,
                 generate_password_hash(pwd), role, hospital_id,
                 session['user_id'], 'approved')
            )
            flash(f'{role.title()} registered successfully', 'success')
    except Exception:
        flash('Registration failed: National ID or Username may already exist', 'error')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/doctor/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def add_doctor():
    return _add_user_core('doctor')


@admin_bp.route('/paramedic/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def add_paramedic():
    return _add_user_core('paramedic')


@admin_bp.route('/patient/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def add_patient():
    return _add_user_core('patient')


@admin_bp.route('/user/approve/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def approve_user(uid):
    with get_db() as db:
        db.execute("UPDATE users SET status='approved' WHERE id=?", (uid,))
    flash('User approved', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/user/reject/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def reject_user(uid):
    with get_db() as db:
        db.execute("UPDATE users SET status='rejected' WHERE id=?", (uid,))
    flash('User rejected', 'success')
    return redirect(url_for('admin.dashboard'))


ALLOWED_ROLES = {'admin', 'doctor', 'paramedic', 'patient'}


@admin_bp.route('/user/role/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def change_role(uid):
    new_role = request.form.get('role', '')
    if new_role not in ALLOWED_ROLES:
        flash('Invalid role', 'error')
        return redirect(url_for('admin.dashboard'))
    # Prevent privilege escalation: only allow admin->admin if already admin
    if new_role == 'admin':
        with get_db() as db:
            current = db.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
            if current and current['role'] != 'admin':
                flash('Cannot promote non-admin users to admin', 'error')
                return redirect(url_for('admin.dashboard'))
    with get_db() as db:
        db.execute("UPDATE users SET role=? WHERE id=?", (new_role, uid))
    flash('Role updated', 'success')
    return redirect(url_for('admin.dashboard'))


# ── Full User Management ───────────────────────────────────────
@admin_bp.route('/users')
@login_required
@role_required('admin')
def users():
    with get_db() as db:
        users = db.execute(
            """SELECT u.*, h.name as hospital_name,
            (SELECT COUNT(*) FROM injury_records WHERE patient_id=u.id) as injury_count,
            (SELECT COUNT(*) FROM qr_scans WHERE patient_id=u.id) as scan_count
            FROM users u LEFT JOIN hospitals h ON u.hospital_id = h.id
            ORDER BY u.role, u.first_name"""
        ).fetchall()
    return render_template('admin_users.html', users=users)


@admin_bp.route('/user/edit/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def edit_user(uid):
    first = request.form.get('first_name', '').strip()
    second = request.form.get('second_name', '').strip()
    phone = request.form.get('phone', '').strip()
    username = request.form.get('username', '').strip()
    nat_id = request.form.get('national_id', '').strip()
    if not all([first, second, username, nat_id]):
        flash('Name, username, and national ID are required', 'error')
        return redirect(url_for('admin.users'))
    with get_db() as db:
        db.execute(
            """UPDATE users SET first_name=?, second_name=?, phone=?, username=?, national_id=?, age=?, hospital_id=? WHERE id=?""",
            (first, second, phone, username, nat_id, int(request.form.get('age', 30)),
             request.form.get('hospital_id') or None, uid)
        )
        if request.form.get('new_password'):
            db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(request.form['new_password']), uid)
            )
        flash('User updated', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/user/delete/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def delete_user(uid):
    """FK cascade handles cleanup. Only null out registered_by references."""
    with get_db() as db:
        db.execute("UPDATE users SET registered_by=NULL WHERE registered_by=?", (uid,))
        db.execute("DELETE FROM users WHERE id=? AND role!='admin'", (uid,))
        flash('User and all associated data deleted', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/user/reset-password/<int:uid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def reset_password(uid):
    pw = request.form.get('new_password', 'changeme123')
    with get_db() as db:
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(pw), uid))
    flash('Password reset', 'success')
    return redirect(url_for('admin.users'))


# ── Patient View ───────────────────────────────────────────────
@admin_bp.route('/patient/<int:pid>')
@login_required
@role_required('admin')
def patient_view(pid):
    from ml_model import predict_risk

    with get_db() as db:
        patient = db.execute("SELECT * FROM users WHERE id=?", (pid,)).fetchone()
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('admin.dashboard'))

        injuries = db.execute(
            """SELECT ir.*, u.first_name || ' ' || u.second_name as writer_name
            FROM injury_records ir JOIN users u ON ir.written_by = u.id
            WHERE ir.patient_id=? ORDER BY ir.created_at DESC""",
            (pid,)
        ).fetchall()

        scans = db.execute(
            "SELECT * FROM qr_scans WHERE patient_id=? ORDER BY scanned_at DESC LIMIT 20",
            (pid,)
        ).fetchall()

        health = db.execute("SELECT * FROM patient_health WHERE patient_id=?", (pid,)).fetchone()

    # ML risk prediction (single query — health already fetched above)
    if health:
        risk = predict_risk(
            age=health['age'], gender=health['gender'], bmi=health['bmi'],
            steps=health['daily_steps'], sleep=health['sleep_hours'],
            water=health['water_intake_l'], calories=health['calories_consumed'],
            smoker=health['smoker'], alcohol=health['alcohol'],
            hr=health['resting_hr'], sys_bp=health['systolic_bp'],
            dia_bp=health['diastolic_bp'], chol=health['cholesterol'],
            family_history=health['family_history']
        )
    else:
        risk = {
            'risk': 1 if injuries else 0,
            'probability': 60.0 if injuries else 10.0,
            'models': {}, 'scores': {},
            'risk_level': 'Moderate' if injuries else 'Low'
        }

    return render_template('admin_patient.html',
                           patient=patient, injuries=injuries,
                           scans=scans, risk=risk, health=health)


@admin_bp.route('/injury/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def add_injury():
    pid = request.form.get('patient_id')
    if not pid:
        flash('Patient ID is required', 'error')
        return redirect(url_for('admin.injuries'))
    with get_db() as db:
        for itype in request.form.get('injury_type', '').split('|'):
            if itype.strip():
                db.execute(
                    """INSERT INTO injury_records
                    (patient_id, injury_type, injury_description, injury_date, severity, treatment, notes, written_by)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (pid, itype.strip(),
                     request.form.get('injury_description', itype.strip()),
                     request.form.get('injury_date', ''),
                     request.form.get('severity', 'medium'),
                     request.form.get('treatment', ''),
                     request.form.get('notes', ''),
                     session['user_id'])
                )
        flash('Injury added', 'success')
    return redirect(url_for('admin.patient_view', pid=pid))


# ── Injuries & Scans & Edits ───────────────────────────────────
@admin_bp.route('/injuries')
@login_required
@role_required('admin')
def injuries():
    with get_db() as db:
        injuries = db.execute(
            """SELECT ir.*, p.first_name||' '||p.second_name as patient_name,
            w.first_name||' '||w.second_name as writer_name
            FROM injury_records ir JOIN users p ON ir.patient_id=p.id JOIN users w ON ir.written_by=w.id
            ORDER BY ir.created_at DESC LIMIT 100"""
        ).fetchall()
        patients = db.execute(
            "SELECT id, first_name, second_name FROM users WHERE role='patient' AND status='approved'"
        ).fetchall()
    return render_template('admin_injuries.html', injuries=injuries, patients=patients)


@admin_bp.route('/injury/delete/<int:rid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def delete_injury(rid):
    with get_db() as db:
        db.execute("DELETE FROM edit_history WHERE injury_record_id=?", (rid,))
        db.execute("DELETE FROM injury_records WHERE id=?", (rid,))
    flash('Injury deleted', 'success')
    return redirect(url_for('admin.injuries'))


@admin_bp.route('/scans')
@login_required
@role_required('admin')
def scans():
    with get_db() as db:
        scans = db.execute(
            """SELECT qs.*, p.first_name||' '||p.second_name as patient_name
            FROM qr_scans qs JOIN users p ON qs.patient_id=p.id ORDER BY qs.scanned_at DESC LIMIT 100"""
        ).fetchall()
        stats = {
            'total': db.execute("SELECT COUNT(*) as c FROM qr_scans").fetchone()['c'],
            'today': db.execute("SELECT COUNT(*) as c FROM qr_scans WHERE date(scanned_at)=date('now')").fetchone()['c'],
        }
    return render_template('admin_scans.html', scans=scans, stats=stats)


@admin_bp.route('/scan/delete/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def delete_scan(sid):
    with get_db() as db:
        db.execute("DELETE FROM qr_scans WHERE id=?", (sid,))
    flash('Scan deleted', 'success')
    return redirect(url_for('admin.scans'))


@admin_bp.route('/edits')
@login_required
@role_required('admin')
def edits():
    with get_db() as db:
        edits = db.execute(
            """SELECT eh.*, u.first_name||' '||u.second_name as editor_name
            FROM edit_history eh JOIN users u ON eh.edited_by=u.id ORDER BY eh.edited_at DESC LIMIT 100"""
        ).fetchall()
    return render_template('admin_edits.html', edits=edits)


# ── Analytics ──────────────────────────────────────────────────
@admin_bp.route('/analytics')
@login_required
@role_required('admin')
def analytics():
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient'").fetchone()['c']
        infected = db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient' AND infection_status=1").fetchone()['c']
        deaths = db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient' AND death_status=1").fetchone()['c']
        exposed = db.execute("SELECT COUNT(*) as c FROM users WHERE role='patient' AND exposed=1").fetchone()['c']
        unexposed = total - exposed

        risk_exposed = infected / exposed if exposed > 0 else 0
        risk_unexposed = db.execute(
            "SELECT COUNT(*) as c FROM users WHERE role='patient' AND exposed=0 AND infection_status=1"
        ).fetchone()['c']
        risk_unexposed_val = risk_unexposed / unexposed if unexposed > 0 else 0.001

        stats = {
            'total_patients': total,
            'infected': infected,
            'deaths': deaths,
            'exposed': exposed,
            'unexposed': unexposed,
            'risk_rate': round((infected / exposed * 100), 2) if exposed > 0 else 0,
            'mortality_rate': round((deaths / infected * 100), 2) if infected > 0 else 0,
            'relative_risk': round(risk_exposed / risk_unexposed_val, 2) if risk_unexposed_val > 0 else 0,
            'infection_rate': round((infected / total * 100), 2) if total > 0 else 0,
        }
        patients = db.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM injury_records WHERE patient_id=u.id) as injury_count
            FROM users u WHERE u.role='patient' ORDER BY u.infection_status DESC, u.first_name"""
        ).fetchall()
    return render_template('admin_analytics.html', stats=stats, patients=patients)


@admin_bp.route('/patient/infection/<int:pid>', methods=['POST'])
@login_required
@role_required('admin', 'doctor')
@csrf_required
def set_infection(pid):
    with get_db() as db:
        db.execute("UPDATE users SET infection_status=? WHERE id=?",
                   (int(request.form.get('status', 0)), pid))
    flash('Updated', 'success')
    return redirect(request.referrer or url_for('admin.dashboard'))


@admin_bp.route('/patient/death/<int:pid>', methods=['POST'])
@login_required
@role_required('admin', 'doctor')
@csrf_required
def set_death(pid):
    with get_db() as db:
        db.execute("UPDATE users SET death_status=? WHERE id=?",
                   (int(request.form.get('status', 0)), pid))
    flash('Updated', 'success')
    return redirect(request.referrer or url_for('admin.dashboard'))


@admin_bp.route('/patient/exposed/<int:pid>', methods=['POST'])
@login_required
@role_required('admin', 'doctor')
@csrf_required
def set_exposed(pid):
    with get_db() as db:
        db.execute("UPDATE users SET exposed=? WHERE id=?",
                   (int(request.form.get('status', 0)), pid))
    flash('Updated', 'success')
    return redirect(request.referrer or url_for('admin.dashboard'))


# ── Export (parameterized, no SQL injection) ───────────────────
_EXPORT_TABLES = {
    'users':           'users',
    'injury_records':  'injury_records',
    'qr_scans':        'qr_scans',
    'edit_history':    'edit_history',
    'hospitals':       'hospitals',
}


@admin_bp.route('/export/<table>')
@login_required
@role_required('admin')
def export(table):
    if table not in _EXPORT_TABLES:
        return "Not allowed", 403

    safe_table = _EXPORT_TABLES[table]
    with get_db() as db:
        rows = db.execute(f"SELECT * FROM {safe_table}").fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(r)
    output.seek(0)
    return Response(
        output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename={safe_table}_export.csv'}
    )


# ── QR Codes Gallery ───────────────────────────────────────────
@admin_bp.route('/qrcodes')
@login_required
@role_required('admin')
def qrcodes():
    with get_db() as db:
        patients = db.execute(
            """SELECT u.*, h.name as hospital_name,
            (SELECT COUNT(*) FROM injury_records WHERE patient_id=u.id) as injury_count,
            (SELECT COUNT(*) FROM qr_scans WHERE patient_id=u.id) as scan_count
            FROM users u LEFT JOIN hospitals h ON u.hospital_id = h.id
            WHERE u.role='patient' AND u.status='approved'
            ORDER BY u.first_name"""
        ).fetchall()
    return render_template('admin_qrcodes.html', patients=patients, BASE_URL=BASE_URL)
