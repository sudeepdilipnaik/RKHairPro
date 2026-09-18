"""RK Hair Pro – central multi-branch salon management app (Flask)."""
import os
import secrets
import threading
import time
from datetime import datetime

from flask import Flask, abort, render_template, request, session, url_for
from flask_login import current_user, logout_user

from .config import Config
from .extensions import db, login_manager
from .helpers import PERIODS, fmt_date, fmt_dt, fmt_time, inr, scope_ids, setting, all_branches, active_branch


def short_num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    a = abs(v)
    if a >= 1e7:
        return f"{v / 1e7:.1f}Cr"
    if a >= 1e5:
        return f"{v / 1e5:.1f}L"
    if a >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    inst = os.path.join(os.path.dirname(app.root_path), "instance")
    os.makedirs(inst, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    # first start on a new machine/server: copy the ready-made sample database instead of building it (which takes ~100 s of CPU)
    live_db = os.path.join(inst, "rkhp.db")
    if not config and not os.path.exists(live_db) and os.path.exists(app.config.get("DEMO_DB", "")):
        import shutil
        shutil.copy(app.config["DEMO_DB"], live_db)
    db.init_app(app)
    login_manager.init_app(app)

    from .models import Employee

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(Employee, int(uid))

    # ---- template helpers ----
    def tel10(v):
        return "".join(c for c in str(v or "") if c.isdigit())[-10:]

    def phone_fmt(v):
        d = tel10(v)
        return f"{d[:5]} {d[5:]}" if len(d) == 10 else (v or "")

    app.jinja_env.filters.update(inr=inr, dfmt=fmt_date, tfmt=fmt_time, dtfmt=fmt_dt, short=short_num, tel10=tel10, phone=phone_fmt)
    app.jinja_env.globals.update(PERIODS=PERIODS, setting=setting, now=datetime.now, all_branches=all_branches, active_branch=active_branch, current_user=current_user)
    # the shared UI macros are available in every template (including includes and imported macros) as `ui`
    app.jinja_env.globals["ui"] = app.jinja_env.get_template("_ui.html").module

    def csrf():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]
    app.jinja_env.globals["csrf"] = csrf

    @app.before_request
    def _guard():
        if request.method == "POST":
            tok = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not tok or tok != session.get("_csrf"):
                abort(400, "Session expired – please reload the page and try again.")
        if current_user.is_authenticated and not current_user.is_active:
            logout_user()

    @app.context_processor
    def _ctx():
        if not current_user.is_authenticated:
            return {}
        return dict(branches=all_branches(), scope_branch=active_branch(), nav_sections=build_nav(), unread_count=unread())

    # ---- blueprints ----
    from .routes import (appointments, auth, billing, clients, dashboard, finance, hr, inventory, marketing, misc, operations,
                         portal, public, reports, settings, api, website_admin)
    for m in (auth, public, dashboard, appointments, clients, billing, finance, hr, portal, inventory, operations, marketing, reports, settings, misc, api, website_admin):
        app.register_blueprint(m.bp)

    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403, msg="You don't have access to this page.", page_title="Access denied"), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, msg="We couldn't find that page.", page_title="Not found"), 404

    @app.errorhandler(400)
    def _400(e):
        return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request"), page_title="Oops"), 400

    with app.app_context():
        db.create_all()
        from .models import Branch
        if Branch.query.count() == 0:
            from . import seed
            print("First run: creating demo data (one-time, about 30-60 seconds)...", flush=True)
            seed.seed(history=True)
            print("Demo data ready.", flush=True)
        if Branch.query.count():
            from .logic.web import ensure_website_content
            ensure_website_content()

    if app.config.get("RUN_SCHEDULER"):
        start_scheduler(app)
    return app


def unread():
    from .models import Notification
    return Notification.query.filter_by(employee_id=current_user.id, is_read=False).count()


def build_nav():
    from .models import Employee, LeaveRequest, Reimbursement, RoleRequest, StockRequest
    u = current_user
    ep = request.endpoint or ""
    bp = ep.split(".")[0]

    def item(label, icon, endpoint, active_bps, count=0, **kw):
        return dict(label=label, icon=icon, href=url_for(endpoint, **kw), active=bp in active_bps, count=count)

    secs = []
    P = u.has_perm
    any_perm = bool(u.perm_set)
    over = []
    if any_perm:
        over.append(item("Dashboard", "home", "dash.index", ("dash",)))
    if u.is_manager:
        cnt = 0
        ids = scope_ids()
        cnt += LeaveRequest.query.join(Employee, Employee.id == LeaveRequest.employee_id).filter(LeaveRequest.status == "pending", Employee.branch_id.in_(ids)).count()
        cnt += Reimbursement.query.filter(Reimbursement.status == "pending", Reimbursement.branch_id.in_(ids)).count()
        cnt += StockRequest.query.filter(StockRequest.status == "pending", StockRequest.branch_id.in_(ids)).count()
        if u.is_super:
            cnt += RoleRequest.query.filter_by(status="pending").count()
        over.append(item("Approvals", "approve", "misc.approvals", ("misc",), cnt if cnt else 0))
    if over:
        secs.append(dict(title="Overview", items=over))
    ops = []
    if P("appointments"):
        ops.append(item("Appointments", "calendar", "appt.board", ("appt",)))
    if P("billing"):
        ops.append(item("Billing / POS", "card", "billing.new", ("billing",)))
    if P("appointments") or P("marketing"):
        ops.append(item("Clients", "users", "clients.index", ("clients",)))
    if P("operations"):
        ops.append(item("Operations", "clipboard", "ops.index", ("ops",)))
    if ops:
        secs.append(dict(title="Salon", items=ops))
    biz = []
    if P("finance"):
        biz.append(item("Finance", "wallet", "fin.index", ("fin",)))
    if P("inventory"):
        biz.append(item("Inventory", "package", "inv.index", ("inv",)))
    if P("reports"):
        biz.append(item("Reports", "chart", "rep.index", ("rep",)))
    if biz:
        secs.append(dict(title="Business", items=biz))
    ppl = []
    if P("hr"):
        ppl.append(item("HR & Payroll", "briefcase", "hr.employees", ("hr",)))
    if P("marketing"):
        ppl.append(item("Marketing", "megaphone", "mkt.index", ("mkt",)))
    if P("website"):
        ppl.append(item("Website Studio", "globe", "web.index", ("web",)))
    if ppl:
        secs.append(dict(title="People & Growth", items=ppl))
    sysm = []
    if u.is_manager:
        sysm.append(item("WhatsApp Outbox", "chat", "misc.outbox", ("misc_x",)))
    if P("settings"):
        sysm.append(item("Settings", "sliders", "sett.index", ("sett",)))
    if sysm:
        secs.append(dict(title="System", items=sysm))
    if not u.is_super:
        me = [item("My Day", "home", "me.home", ("me",)), item("Earnings & Tips", "wallet", "me.earnings", ("me_x",)),
              item("Leave", "calendar", "me.leave", ("me_y",)), item("Reimbursements", "card", "me.reimb", ("me_z",)),
              item("Announcements", "megaphone", "me.announcements", ("me_w",))]
        secs.append(dict(title="My Portal", items=me))
    # exact-endpoint active highlighting for the portal & outbox items
    for s in secs:
        for it in s["items"]:
            if it["href"] in (url_for("me.earnings"), url_for("me.leave"), url_for("me.reimb"), url_for("me.announcements"), url_for("misc.outbox"), url_for("misc.approvals")):
                it["active"] = request.path == it["href"]
            if it["href"] == url_for("me.home"):
                it["active"] = request.path == it["href"]
    return secs


_sched_started = False


def start_scheduler(app):
    """Background job: WhatsApp reminders one hour before each appointment."""
    global _sched_started
    if _sched_started:
        return
    _sched_started = True

    def loop():
        from .logic import whatsapp as wa
        time.sleep(5)
        while True:
            try:
                with app.app_context():
                    wa.send_due_reminders()
            except Exception as exc:   # never let the job thread die
                print("scheduler error:", exc, flush=True)
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="reminders").start()
