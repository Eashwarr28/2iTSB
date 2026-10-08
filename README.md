# 2ITSB - Smart Insulin Injection Therapy Management System

## Setup

1. Create a virtual environment (PyCharm does this automatically)
2. Install dependencies:
   pip install -r requirements.txt
3. Run:
   python app.py
4. Open: http://127.0.0.1:5000

## Demo accounts (password: 123456)
- patient@2itsb.demo   (Patient)
- caregiver@2itsb.demo (Caregiver) -> pair with code ABC123
- nurse@2itsb.demo     (Nurse)
- doctor@2itsb.demo    (Doctor)

## Files
- app.py                  - Flask application + models + routes
- templates/*.html        - Jinja2 templates
- static/css/style.css    - Stylesheet
- requirements.txt        - Python dependencies
