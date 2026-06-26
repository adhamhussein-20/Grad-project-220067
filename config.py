"""MediTrack Configuration v3.0 — PythonAnywhere"""
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, 'meditrack.db')
MODEL_PATH = os.path.join(BASE_DIR, 'disease_risk_model.pkl')
SCALER_PATH = os.path.join(BASE_DIR, 'disease_risk_scaler.pkl')

_E = os.path.join(BASE_DIR, '.env')
if os.path.exists(_E):
    for _line in open(_E):
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

SECRET_KEY = os.environ.get('SECRET_KEY', 'fallback-meditrack-key-change-me')
BASE_URL = os.environ.get('BASE_URL', 'https://hapx.pythonanywhere.com')
DATASET_PATH = os.environ.get('DATASET_PATH', os.path.join(BASE_DIR, 'dataset.csv'))
PASSWORD_MIN_LENGTH = int(os.environ.get('PASSWORD_MIN_LENGTH', '8'))
PASSWORD_REQUIRE_UPPER = os.environ.get('PASSWORD_REQUIRE_UPPER', '1') == '1'
PASSWORD_REQUIRE_DIGIT = os.environ.get('PASSWORD_REQUIRE_DIGIT', '1') == '1'
PASSWORD_REQUIRE_SPECIAL = os.environ.get('PASSWORD_REQUIRE_SPECIAL', '0') == '1'
LOGIN_MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', '5'))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get('LOGIN_LOCKOUT_MINUTES', '15'))
RATE_LIMIT_WINDOW = int(os.environ.get('RATE_LIMIT_WINDOW', '60'))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get('RATE_LIMIT_MAX_REQUESTS', '30'))
ML_RISK_LOW_THRESHOLD = float(os.environ.get('ML_RISK_LOW_THRESHOLD', '30'))
ML_RISK_HIGH_THRESHOLD = float(os.environ.get('ML_RISK_HIGH_THRESHOLD', '60'))
QR_REQUIRE_AUTH = os.environ.get('QR_REQUIRE_AUTH', '0') == '1'
