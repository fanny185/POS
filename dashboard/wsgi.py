# Entry point for gunicorn (production web server)
# Render/Railway runs:  gunicorn dashboard.wsgi:app
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.app import app
