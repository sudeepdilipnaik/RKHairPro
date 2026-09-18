# ---- Paste THIS into your PythonAnywhere WSGI file (Web tab -> "WSGI configuration file") ----
# Replace YOURUSERNAME (twice) and the secret text.
import os
import sys

project = "/home/YOURUSERNAME/RKHairPro"
if project not in sys.path:
    sys.path.insert(0, project)

os.environ["RKHP_SECRET"] = "PUT-A-LONG-RANDOM-TEXT-HERE"   # signs login sessions - keep private
os.environ["RKHP_HTTPS"] = "1"                              # PythonAnywhere serves https
os.environ["RKHP_SCHEDULER"] = "0"                          # background threads are not reliable on free hosting
os.environ["RKHP_DEMO"] = "1"                               # 1 = show one-click demo logins, 0 = hide them (use 0 for real launch)

from rkhp import create_app          # noqa: E402

application = create_app()
