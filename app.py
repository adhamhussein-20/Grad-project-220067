"""MediTrack v3.0 — WSGI entry point for PythonAnywhere."""
import sys

# Force DB init before anything else
from database import init_db
init_db()

from flask import Flask
from config import SECRET_KEY

app = Flask(__name__)
app.secret_key = SECRET_KEY

from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.doctor import doctor_bp
from routes.patient import patient_bp

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(doctor_bp)
app.register_blueprint(patient_bp)

@app.route('/')
def home():
    return '<h1>MediTrack v3.0 is working!</h1>'

application = app
