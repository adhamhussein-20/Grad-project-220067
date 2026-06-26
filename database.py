"""Database connection, initialization, migrations, and seeding."""
import sqlite3, datetime as _dt, random, os
from contextlib import contextmanager
from config import DB, BASE_DIR, LOGIN_MAX_ATTEMPTS


@contextmanager
def get_db():
    """Context-manager-safe DB connection. Use: with get_db() as db: ..."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema version (SQLite user_version pragma) ──
CURRENT_SCHEMA_VERSION = 3


def _get_schema_version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


def _set_schema_version(db, version):
    db.execute(f"PRAGMA user_version = {version}")


def init_db():
    """Create tables, run migrations, seed demo data if needed."""
    with get_db() as db:
        version = _get_schema_version(db)

        if version < 1:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS hospitals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                location TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                national_id TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                first_name TEXT NOT NULL,
                second_name TEXT NOT NULL,
                age INTEGER DEFAULT 30,
                phone TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','doctor','paramedic','patient')),
                status TEXT DEFAULT 'approved' CHECK(status IN ('pending','approved','rejected')),
                hospital_id INTEGER REFERENCES hospitals(id) ON DELETE SET NULL,
                registered_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                infection_status INTEGER DEFAULT 0,
                death_status INTEGER DEFAULT 0,
                exposed INTEGER DEFAULT 0,
                login_attempts INTEGER DEFAULT 0,
                locked_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS injury_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                injury_type TEXT NOT NULL,
                injury_description TEXT NOT NULL,
                injury_date TEXT NOT NULL,
                severity TEXT CHECK(severity IN ('low','medium','high','critical')),
                treatment TEXT,
                notes TEXT,
                written_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS qr_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                scanned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                paramedic_name TEXT,
                paramedic_details TEXT,
                scan_location TEXT,
                action_taken TEXT,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS edit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                injury_record_id INTEGER NOT NULL REFERENCES injury_records(id) ON DELETE CASCADE,
                edited_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                old_data TEXT,
                new_data TEXT,
                edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS patient_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                age INTEGER DEFAULT 30,
                gender INTEGER DEFAULT 1,
                bmi REAL DEFAULT 25.0,
                daily_steps INTEGER DEFAULT 7000,
                sleep_hours REAL DEFAULT 7.0,
                water_intake_l REAL DEFAULT 2.5,
                calories_consumed INTEGER DEFAULT 2000,
                smoker INTEGER DEFAULT 0,
                alcohol INTEGER DEFAULT 0,
                resting_hr INTEGER DEFAULT 75,
                systolic_bp INTEGER DEFAULT 120,
                diastolic_bp INTEGER DEFAULT 80,
                cholesterol INTEGER DEFAULT 200,
                family_history INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            _set_schema_version(db, 1)

        # Migration v1→v2: add status column
        if version < 2:
            for col, default in [('status', "'approved'"), ('age', '30')]:
                try:
                    db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}")
                except sqlite3.OperationalError:
                    pass
            _set_schema_version(db, 2)

        # Migration v2→v3: add login_attempts and locked_until
        if version < 3:
            for col, dtype in [('login_attempts', 'INTEGER DEFAULT 0'), ('locked_until', 'TIMESTAMP')]:
                try:
                    db.execute(f"ALTER TABLE users ADD COLUMN {col} {dtype}")
                except sqlite3.OperationalError:
                    pass
            _set_schema_version(db, 3)

        # ── Seed admin + demo users ──
        _seed_users(db)

        # ── Seed demo injury & scan data ──
        _seed_demo_data(db)


def _seed_users(db):
    """Create admin if missing; seed demo users on fresh install."""
    from werkzeug.security import generate_password_hash

    admin = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin:
        db.execute(
            """INSERT INTO users (national_id, username, first_name, second_name, phone, password_hash, role)
            VALUES (?,?,?,?,?,?,?)""",
            ('00000000000000', 'admin', 'System', 'Admin', '0000000000',
             generate_password_hash('admin123'), 'admin')
        )

    user_count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
    if user_count == 1:
        demo_users = [
            ('12345678901234', 'dr_ahmed', 'Ahmed', 'Hassan', 42, '01001111111', 'doctor123', 'doctor'),
            ('12345678901235', 'pm_omar', 'Omar', 'Sayed', 30, '01002222222', 'medic123', 'paramedic'),
            ('12345678901236', 'pt_mohamed', 'Mohamed', 'Ali', 28, '01003333333', 'patient123', 'patient'),
            ('12345678901237', 'dr_sara', 'Sara', 'Nabil', 35, '01004444444', 'doctor123', 'doctor'),
        ]
        for nat_id, uname, first, second, age, phone, pw, role in demo_users:
            try:
                db.execute(
                    "INSERT INTO users (national_id, username, first_name, second_name, age, phone, password_hash, role, status) VALUES (?,?,?,?,?,?,?,?,?)",
                    (nat_id, uname, first, second, age, phone, generate_password_hash(pw), role, 'approved'))
            except Exception:
                pass
        print('[SEED] Created 4 demo users (2 doctors, 1 paramedic, 1 patient)')


def _seed_demo_data(db):
    """Create demo hospitals, injuries, scans on first run."""
    # Hospitals
    h_count = db.execute("SELECT COUNT(*) as c FROM hospitals").fetchone()['c']
    if h_count == 0:
        for name, loc in [('MSA University Hospital', '6th of October City'),
                          ('Kasr Al Ainy', 'Cairo'),
                          ('Alexandria Main Hospital', 'Alexandria')]:
            db.execute("INSERT INTO hospitals (name, location) VALUES (?,?)", (name, loc))
        print('[SEED] Created 3 demo hospitals')

    # Injuries for demo patient
    patient = db.execute("SELECT id FROM users WHERE role='patient' AND status='approved' LIMIT 1").fetchone()
    injury_count = db.execute("SELECT COUNT(*) as c FROM injury_records").fetchone()['c']
    if patient and injury_count == 0:
        injuries = [
            ('Fracture', 'Left radius fracture from fall', '2026-04-15', 'high', 'Cast applied', 'Follow up in 4 weeks'),
            ('Laceration', 'Deep cut on right palm', '2026-03-20', 'medium', 'Stitches (5)', 'Healing well'),
            ('Sprain', 'Right ankle sprain', '2026-05-01', 'low', 'RICE protocol', ''),
        ]
        writer = db.execute("SELECT id FROM users WHERE role='doctor' LIMIT 1").fetchone()
        writer_id = writer['id'] if writer else 1
        for itype, desc, date, sev, treatment, notes in injuries:
            db.execute(
                """INSERT INTO injury_records (patient_id, injury_type, injury_description, injury_date, severity, treatment, notes, written_by)
                VALUES (?,?,?,?,?,?,?,?)""",
                (patient['id'], itype, desc, date, sev, treatment, notes, writer_id))
        print(f'[SEED] Created {len(injuries)} demo injuries for patient #{patient["id"]}')

    # Demo scan data
    scan_count = db.execute("SELECT COUNT(*) as c FROM qr_scans").fetchone()['c']
    patients = db.execute("SELECT id FROM users WHERE role='patient' AND status='approved'").fetchall()
    if scan_count == 0 and patients:
        today = _dt.date.today()
        pids = [p['id'] for p in patients[:min(3, len(patients))]]
        locations = ['Cairo', 'Giza', 'Alexandria', 'Nasr City']
        actions = ['Viewed', 'Treated', 'Transported', 'Referred']
        seeded = 0
        for day_offset in range(7):
            d = (today - _dt.timedelta(days=day_offset)).isoformat()
            for _ in range(random.randint(0, 3)):
                pid = random.choice(pids)
                db.execute(
                    "INSERT INTO qr_scans (patient_id, scanned_by, paramedic_name, scan_location, action_taken, scanned_at) VALUES (?,?,?,?,?,?)",
                    (pid, None, 'Demo Paramedic', random.choice(locations),
                     random.choice(actions), f'{d} {random.randint(8,22):02d}:{random.randint(0,59):02d}:00'))
                seeded += 1
        print(f'[SEED] Created {seeded} demo QR scan records')

    # Patient health for demo patient
    if patient:
        health_exists = db.execute("SELECT id FROM patient_health WHERE patient_id=?", (patient['id'],)).fetchone()
        if not health_exists:
            db.execute("""INSERT INTO patient_health
                (patient_id, age, gender, bmi, daily_steps, sleep_hours, water_intake_l,
                 calories_consumed, smoker, alcohol, resting_hr, systolic_bp, diastolic_bp,
                 cholesterol, family_history)
                VALUES (?,28,1,24.5,8500,7.5,2.8,2200,0,0,72,118,78,185,0)""",
                (patient['id'],))
            print('[SEED] Created patient health profile for demo patient')


# ── Rate limiting helpers ──
def check_login_lockout(national_id):
    """Check if user is locked out. Returns (is_locked: bool, message: str)."""
    with get_db() as db:
        user = db.execute(
            "SELECT login_attempts, locked_until FROM users WHERE national_id=?",
            (national_id,)
        ).fetchone()
        if not user:
            return False, ''
        if user['locked_until']:
            from datetime import datetime
            try:
                locked = datetime.fromisoformat(user['locked_until'])
                if locked > datetime.utcnow():
                    mins = int((locked - datetime.utcnow()).total_seconds() / 60) + 1
                    return True, f'Account locked. Try again in {mins} minute(s).'
            except (ValueError, TypeError):
                pass
        if user['login_attempts'] >= LOGIN_MAX_ATTEMPTS:
            # Lock if over threshold but no lock timestamp set
            from config import LOGIN_LOCKOUT_MINUTES
            lock_until = (_dt.datetime.utcnow() +
                         _dt.timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
            db.execute("UPDATE users SET locked_until=? WHERE national_id=?",
                       (lock_until, national_id))
            return True, f'Too many failed attempts. Locked for {LOGIN_LOCKOUT_MINUTES} minutes.'
    return False, ''


def record_login_failure(national_id):
    """Increment failed login counter and set lockout if threshold exceeded."""
    from config import LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_MINUTES
    with get_db() as db:
        db.execute(
            "UPDATE users SET login_attempts = login_attempts + 1 WHERE national_id=?",
            (national_id,)
        )
        user = db.execute(
            "SELECT login_attempts FROM users WHERE national_id=?", (national_id,)
        ).fetchone()
        if user and user['login_attempts'] >= LOGIN_MAX_ATTEMPTS:
            lock_until = (_dt.datetime.utcnow() +
                         _dt.timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
            db.execute("UPDATE users SET locked_until=? WHERE national_id=?",
                       (lock_until, national_id))


def reset_login_attempts(national_id):
    """Clear failed attempts after successful login."""
    with get_db() as db:
        db.execute(
            "UPDATE users SET login_attempts=0, locked_until=NULL WHERE national_id=?",
            (national_id,)
        )
