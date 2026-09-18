"""Start RK Hair Pro locally:  python run.py   ->  http://127.0.0.1:5000"""
from rkhp import create_app

app = create_app()

if __name__ == "__main__":
    print("\n  RK Hair Pro is running:  http://127.0.0.1:5000  (public site)   |   http://127.0.0.1:5000/login  (staff login)\n", flush=True)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)
