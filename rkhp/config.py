import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    SECRET_KEY = os.environ.get("RKHP_SECRET", "rk-hair-pro-local-dev-key")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "instance", "rkhp.db").replace("\\", "/")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 300 * 1024 * 1024        # room for website videos
    DEMO_MODE = os.environ.get("RKHP_DEMO", "1") == "1"            # one-click demo logins on the login page (set RKHP_DEMO=0 for a real launch)
    DEFAULT_PASSWORD = "Gold@123"                                   # password given to every seeded (demo) user
    RUN_SCHEDULER = os.environ.get("RKHP_SCHEDULER", "1") == "1"    # background WhatsApp reminder job
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("RKHP_HTTPS", "0") == "1"   # set RKHP_HTTPS=1 when served over https
    DEMO_DB = os.path.join(BASE_DIR, "demo_data", "rkhp_demo.db")       # ready-made sample database (used when no database exists yet)
