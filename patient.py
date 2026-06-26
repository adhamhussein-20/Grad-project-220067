"""Patient, Paramedic & QR routes."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from decorators import login_required, role_required, csrf_required
from database import get_db
from config import BASE_URL, QR_REQUIRE_AUTH
from ml_model import predict_risk
import io, qrcode as qr

patient_bp = Blueprint('patient', __name__, url_prefix='/patient')


def _get_risk_for_patient(pid, age_default=30):
    """Load patient health data from DB and run ML prediction."""
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


# ── Patient dashboard ───────────────────────────────────────────
@patient_bp.route('/dashboard')
@login_required
@role_required('patient')
def dashboard():
    uid = session['user_id']
    with get_db() as db:
        patient = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        injuries = db.execute(
            """SELECT ir.*, u.first_name||' '||u.second_name as writer_name,
            u.role as writer_role
            FROM injury_records ir JOIN users u ON ir.written_by=u.id
            WHERE ir.patient_id=? ORDER BY ir.created_at DESC""",
            (uid,)
        ).fetchall()
        scans = db.execute(
            "SELECT * FROM qr_scans WHERE patient_id=? ORDER BY scanned_at DESC LIMIT 10",
            (uid,)
        ).fetchall()
        health = db.execute("SELECT * FROM patient_health WHERE patient_id=?", (uid,)).fetchone()

    risk, health_data = _get_risk_for_patient(uid, age_default=patient['age'] if patient['age'] else 30)
    if risk is None:
        risk = {'risk': 0, 'probability': 0.0, 'models': {}, 'scores': {}, 'risk_level': 'Unknown'}

    return render_template('patient_dashboard.html',
                           patient=patient, injuries=injuries, scans=scans,
                           risk=risk, health=health)


# ── Save / update patient health data ──────────────────────────
@patient_bp.route('/health', methods=['POST'])
@login_required
@role_required('patient', 'doctor', 'admin')
@csrf_required
def update_health():
    pid = request.form.get('patient_id') or session['user_id']
    try:
        age = int(request.form.get('age', 30))
        gender = int(request.form.get('gender', 1))
        bmi = float(request.form.get('bmi', 25.0))
        steps = int(request.form.get('daily_steps', 7000))
        sleep = float(request.form.get('sleep_hours', 7.0))
        water = float(request.form.get('water_intake_l', 2.5))
        cal = int(request.form.get('calories_consumed', 2000))
        smoker = int(request.form.get('smoker', 0))
        alc = int(request.form.get('alcohol', 0))
        hr = int(request.form.get('resting_hr', 75))
        sbp = int(request.form.get('systolic_bp', 120))
        dbp = int(request.form.get('diastolic_bp', 80))
        chol = int(request.form.get('cholesterol', 200))
        fam = int(request.form.get('family_history', 0))
    except (ValueError, TypeError):
        flash('Invalid health data — please check all fields.', 'error')
        return redirect(request.referrer or url_for('patient.dashboard'))

    with get_db() as db:
        existing = db.execute("SELECT id FROM patient_health WHERE patient_id=?", (pid,)).fetchone()
        if existing:
            db.execute(
                """UPDATE patient_health
                SET age=?, gender=?, bmi=?, daily_steps=?, sleep_hours=?,
                    water_intake_l=?, calories_consumed=?, smoker=?, alcohol=?,
                    resting_hr=?, systolic_bp=?, diastolic_bp=?, cholesterol=?,
                    family_history=?, updated_at=CURRENT_TIMESTAMP
                WHERE patient_id=?""",
                (age, gender, bmi, steps, sleep, water, cal, smoker, alc,
                 hr, sbp, dbp, chol, fam, pid))
        else:
            db.execute(
                """INSERT INTO patient_health
                (patient_id, age, gender, bmi, daily_steps, sleep_hours,
                 water_intake_l, calories_consumed, smoker, alcohol,
                 resting_hr, systolic_bp, diastolic_bp, cholesterol, family_history)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, age, gender, bmi, steps, sleep, water, cal, smoker, alc,
                 hr, sbp, dbp, chol, fam))
    flash('Health profile updated. Risk assessment recalculated.', 'success')

    redirect_to = request.form.get('redirect_to', '')
    if redirect_to == 'doctor' and session.get('role') in ('doctor', 'admin'):
        return redirect(url_for('doctor.patient_view', pid=pid))
    if redirect_to == 'admin':
        return redirect(url_for('admin.patient_view', pid=pid))
    return redirect(url_for('patient.dashboard'))


# ── QR Code view (optionally requires auth in production) ──────
@patient_bp.route('/qr/<int:pid>')
def qr_view(pid):
    # If QR_REQUIRE_AUTH is enabled, require login
    if QR_REQUIRE_AUTH and 'user_id' not in session:
        session['next_url'] = request.url
        return redirect(url_for('auth.login'))

    with get_db() as db:
        user = db.execute(
            """SELECT u.first_name, u.second_name, u.national_id, u.phone, u.role, u.age,
            (SELECT COUNT(*) FROM injury_records WHERE patient_id=u.id) as injury_count
            FROM users u WHERE u.id=?""",
            (pid,)
        ).fetchone()
        if not user:
            return "Patient not found", 404

        injuries = []
        if user['role'] == 'patient':
            injuries = db.execute(
                """SELECT ir.*, u2.first_name||' '||u2.second_name as doctor_name
                FROM injury_records ir JOIN users u2 ON ir.written_by=u2.id
                WHERE ir.patient_id=? ORDER BY ir.injury_date DESC""",
                (pid,)
            ).fetchall()

    risk, health_data = _get_risk_for_patient(pid, age_default=user['age'] or 30)
    if risk is None:
        risk = {
            'risk': 1 if injuries else 0,
            'probability': 60.0 if injuries else 10.0,
            'models': {}, 'scores': {},
            'risk_level': 'Moderate' if injuries else 'Low'
        }

    return render_template('qr_view.html',
                           user=user, injuries=injuries, pid=pid,
                           risk=risk, health=health_data, BASE_URL=BASE_URL)


# ── QR Code image (public — same optional auth) ─────────────────
@patient_bp.route('/api/patient/<int:pid>/qrcode')
def patient_qrcode(pid):
    if QR_REQUIRE_AUTH and 'user_id' not in session:
        return "Authentication required", 401

    url = f'{BASE_URL}/patient/qr/{pid}'
    qr_obj = qr.QRCode(
        version=None,
        error_correction=qr.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr_obj.add_data(url)
    qr_obj.make(fit=True)
    img = qr_obj.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', max_age=3600)


# ── Patient search API ─────────────────────────────────────────
@patient_bp.route('/api/search_patient')
@login_required
@role_required('paramedic', 'doctor', 'admin')
def search_patient():
    q = request.args.get('q', '').strip()
    with get_db() as db:
        results = db.execute(
            """SELECT id, first_name, second_name, national_id, phone, role
            FROM users WHERE (national_id LIKE ? OR first_name LIKE ? OR second_name LIKE ?)
            AND status='approved' LIMIT 10""",
            (f'%{q}%', f'%{q}%', f'%{q}%')
        ).fetchall()
    return jsonify([dict(r) for r in results])


# ── Paramedic ──────────────────────────────────────────────────
@patient_bp.route('/paramedic')
@login_required
@role_required('paramedic')
def paramedic_dashboard():
    return render_template('paramedic_dashboard.html')


@patient_bp.route('/paramedic/scan', methods=['POST'])
@login_required
@role_required('paramedic')
@csrf_required
def paramedic_scan():
    pid = request.form.get('patient_id')
    if not pid:
        flash('No patient ID provided. Please scan a valid QR code.', 'error')
        return redirect(url_for('patient.paramedic_dashboard'))
    with get_db() as db:
        patient = db.execute("SELECT * FROM users WHERE id=? AND role='patient'", (pid,)).fetchone()
        if patient:
            db.execute(
                """INSERT INTO qr_scans
                (patient_id, scanned_by, paramedic_name, paramedic_details, scan_location, action_taken)
                VALUES (?,?,?,?,?,?)""",
                (pid, session['user_id'], session['name'],
                 f"Paramedic ID: {session['user_id']}",
                 request.form.get('scan_location', ''),
                 request.form.get('action_taken', 'Viewed'))
            )
            flash(f'Scan logged for {patient["first_name"]} {patient["second_name"]}', 'success')
        else:
            flash('Patient not found. Check the QR code and try again.', 'error')
    return redirect(url_for('patient.paramedic_dashboard'))
