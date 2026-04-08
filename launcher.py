import sys
import os
import threading
import webbrowser
import time

# Fix paths when running as PyInstaller bundle
if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    os.chdir(os.path.dirname(sys.executable))
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# Make app.py find templates/ and groovy_runner/ correctly
os.environ['FLASK_BASE_DIR'] = base_dir

from app import app

def open_browser():
    time.sleep(1.5)
    webbrowser.open('http://127.0.0.1:5000')

threading.Thread(target=open_browser, daemon=True).start()
app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)