"""Doctor routes — Patient management, injury records, paramedic management."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash
from decorators import login_required, role_required, csrf_required
from database import get_db
from ml_model import predict_risk
from config import PASSWORD_MIN_LENGTH
from routes.auth import _validate_password

doctor_bp = Blueprint('doctor', __name__, url_prefix='/doctor')


def _get_patient_risk(pid):
    """Load patient health data from DB and run ML prediction. Returns (risk_dict, health_row)."""
    with get_db() as db:
        h = db.execute("SELECT * FROM patient_health WHERE patient_id=?", (pid,)).fetchone()
    if h:
        return predict_risk(
            age=h['age'], gender=h['gender'], bmi=h['bmi'],
            steps=h['daily_steps'], sleep=h['sleep_hours'],
            water=h['water_intake_l'], calories=h['calories_consumed'],
            smoker=h['smoker'], alcohol=h['alcohol'],
            hr=h['resting_hr'], sys_bp=h['systolic_bp'],
            dia_bp=h['diastolic_bp'], chol=h['cholesterol'],
            family_history=h['family_history']
        ), dict(h)
    return None, None


@doctor_bp.route('/')
@login_required
@role_required('doctor')
def dashboard():
    with get_db() as db:
        patients = db.execute(
            "SELECT * FROM users WHERE role='patient' AND status='approved' ORDER BY created_at DESC"
        ).fetchall()
        my_patients = db.execute(
            """SELECT DISTINCT u.* FROM users u
            JOIN injury_records ir ON u.id=ir.patient_id
            WHERE ir.written_by=? ORDER BY u.first_name""",
            (session['user_id'],)
        ).fetchall()
        paramedics = db.execute(
            """SELECT u.*, h.name as hospital_name
            FROM users u LEFT JOIN hospitals h ON u.hospital_id=h.id
            WHERE u.role='paramedic' AND u.registered_by=?""",
            (session['user_id'],)
        ).fetchall()
    return render_template('doctor_dashboard.html',
                           patients=patients, my_patients=my_patients, paramedics=paramedics)


@doctor_bp.route('/paramedic/add', methods=['POST'])
@login_required
@role_required('doctor')
@csrf_required
def add_paramedic():
    nat_id = request.form.get('national_id', '').strip()
    username = request.form.get('username', '').strip()
    first = request.form.get('first_name', '').strip()
    second = request.form.get('second_name', '').strip()
    phone = request.form.get('phone', '').strip()
    pwd = request.form.get('password', '')

    errors = []
    if not all([nat_id, username, first, second, phone, pwd]):
        errors.append('All fields are required')
    if not nat_id or len(nat_id) != 14 or not nat_id.isdigit():
        errors.append('National ID must be exactly 14 digits')
    if len(pwd) < PASSWORD_MIN_LENGTH:
        errors.append(f'Password must be at least {PASSWORD_MIN_LENGTH} characters')
    errors.extend(_validate_password(pwd))

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('doctor.dashboard'))
    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO users
                (national_id, username, first_name, second_name, phone, password_hash, role, hospital_id, registered_by)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (nat_id, request.form.get('username', '').strip(),
                 request.form.get('first_name', '').strip(),
                 request.form.get('second_name', '').strip(),
                 request.form.get('phone', '').strip(),
                 generate_password_hash(pwd),
                 'paramedic', session.get('hospital_id'), session['user_id'])
            )
            flash('Paramedic registered', 'success')
    except Exception:
        flash('Registration failed: National ID or Username already exists', 'error')
    return redirect(url_for('doctor.dashboard'))


@doctor_bp.route('/patient/<int:pid>')
@login_required
@role_required('doctor', 'admin')
def patient_view(pid):
    with get_db() as db:
        patient = db.execute("SELECT * FROM users WHERE id=?", (pid,)).fetchone()
        injuries = db.execute(
            """SELECT ir.*, u.first_name||' '||u.second_name as writer_name
            FROM injury_records ir JOIN users u ON ir.written_by=u.id
            WHERE ir.patient_id=? ORDER BY ir.created_at DESC""",
            (pid,)
        ).fetchall()
        scans = db.execute(
            "SELECT * FROM qr_scans WHERE patient_id=? ORDER BY scanned_at DESC LIMIT 20",
            (pid,)
        ).fetchall()
        # Health data is fetched once by _get_patient_risk — no duplicate query

    risk, health_data = _get_patient_risk(pid)
    if risk is None:
        risk = {'risk': 0, 'probability': 0.0, 'models': {}, 'scores': {}, 'risk_level': 'Unknown'}

    return render_template('doctor_patient.html',
                           patient=patient, injuries=injuries, scans=scans,
                           risk=risk, health=health_data)


@doctor_bp.route('/injury/add', methods=['POST'])
@login_required
@role_required('doctor', 'admin')
@csrf_required
def add_injury():
    pid = request.form.get('patient_id')
    if not pid:
        flash('Patient ID is required', 'error')
        return redirect(url_for('doctor.dashboard'))
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
        flash('Injury record added successfully', 'success')
    return redirect(url_for('doctor.patient_view', pid=pid))


@doctor_bp.route('/injury/ai-triage', methods=['POST'])
@login_required
@role_required('doctor', 'admin')
@csrf_required
def ai_triage():
    if request.is_json:
        desc = (request.json or {}).get('injury_description', '').lower()
        pid = (request.json or {}).get('patient_id')
    else:
        desc = request.form.get('injury_description', '').lower()
        pid = request.form.get('patient_id')

    # Get ML risk if patient has health data
    risk_result = predict_risk()
    if pid:
        try:
            risk_result, _ = _get_patient_risk(int(pid))
            if risk_result is None:
                risk_result = predict_risk()
        except (ValueError, Exception):
            pass

    # Keyword + ML severity classification
    critical_keywords = ['fracture', 'severe', 'bleed', 'critical', 'cardiac', 'stroke', 'gunshot']
    if any(k in desc for k in critical_keywords):
        sev = 'critical'
    elif any(k in desc for k in ['pain', 'injury', 'wound', 'trauma']):
        ml_prob = risk_result.get('probability', 50) if isinstance(risk_result, dict) else 50
        sev = 'high' if ml_prob > 60 else 'medium'
    elif isinstance(risk_result, dict) and risk_result.get('risk') == 1:
        sev = 'medium'
    else:
        sev = 'low'

    return jsonify({
        'severity': sev,
        'confidence': risk_result.get('probability', 75.0) if isinstance(risk_result, dict) else 75.0,
        'risk_level': risk_result.get('risk_level', 'Unknown') if isinstance(risk_result, dict) else 'Unknown',
    })


@doctor_bp.route('/injury/edit/<int:rid>', methods=['POST'])
@login_required
@role_required('doctor', 'admin')
@csrf_required
def edit_injury(rid):
    with get_db() as db:
        old = db.execute("SELECT * FROM injury_records WHERE id=?", (rid,)).fetchone()
        if old:
            import json
            db.execute(
                "INSERT INTO edit_history (injury_record_id, edited_by, old_data) VALUES (?,?,?)",
                (rid, session['user_id'], json.dumps(dict(old), default=str))
            )
            db.execute(
                """UPDATE injury_records
                SET injury_type=?, injury_description=?, injury_date=?,
                    severity=?, treatment=?, notes=?
                WHERE id=?""",
                (request.form['injury_type'], request.form['injury_description'],
                 request.form['injury_date'], request.form.get('severity'),
                 request.form.get('treatment'), request.form.get('notes'), rid)
            )
            flash('Injury updated', 'success')
        pid = db.execute("SELECT patient_id FROM injury_records WHERE id=?", (rid,)).fetchone()
    return redirect(url_for('doctor.patient_view', pid=pid['patient_id']) if pid else url_for('doctor.dashboard'))
