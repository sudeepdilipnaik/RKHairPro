"""Shared helpers: formatting, settings, branch scope, permissions, audit, notifications."""
import calendar
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import abort, flash, has_request_context, redirect, request, session, url_for
from flask_login import current_user

from .extensions import db
from .models import AuditLog, Branch, Employee, Notification, Setting

DEFAULT_SETTINGS = {
    "business_name": "RK Hair Pro",
    "tagline": "Premium Unisex Salon · South Mumbai",
    "instagram": "https://instagram.com/rkhairpro",          # SAMPLE - replace in Settings > Contact & social
    "facebook": "https://facebook.com/rkhairpro",            # SAMPLE
    "youtube": "",
    "phone_central": "9820011000",                           # SAMPLE central / bookings number
    "whatsapp_number": "9820011000",                         # SAMPLE
    "wa_greeting": "Hi RK Hair Pro! I'd like to book an appointment.",
    "email": "",
    "website": "",
    "google_review_link": "https://g.page/r/rk-hair-pro/review",
    "owner_name": "Rakesh Kumar",
    "fy_start_month": "4",
    "gst_enabled": "0",
    "gst_rate": "18",
    "gstin": "",
    "loyalty_earn_per": "100",        # 1 point per Rs.100 spent
    "loyalty_point_value": "1",       # 1 point = Rs.1
    "grace_minutes": "15",
    "late_marks_per_halfday": "3",
    "early_exits_per_halfday": "3",
    "half_day_min_hours": "4",
    "full_day_min_hours": "7",
    "paid_leaves_per_month": "1",
    "salary_days_basis": "30",
    "default_salary": "18000",
    "default_commission": "10",
    "booking_advance_days": "120",
    "booking_min_notice_min": "30",
    "reminder_minutes": "60",
    # ---- public website content (editable in Website Studio) ----
    "hero_video": "default",             # 'default' = bundled RK logo animation, '' = static logo, or an uploaded file name
    "hero_loop": "0", "hero_sound_btn": "1",
    "site_hero_eyebrow": "Premium unisex salon · South Mumbai",
    "site_hero_title": "Every cut crafted.", "site_hero_title_gold": "Never rushed.",
    "site_hero_lead": "Appointment-only styling by RK and a hand-picked team – precision cuts, signature colour, keratin, grooming and bridal. Pick your branch, stylist and time in under a minute.",
    "site_meet_eyebrow": "Meet the founder",
    "site_meet_text": "RK works by appointment only – so when you're in his chair, you have his full attention. He leads the team at both branches and personally guides every signature look.",
    "site_meet_points": "Appointment-only – no waiting, no rush\nA hand-picked team of senior stylists and beauty experts\nSterilised tools and professional-grade products",
    "site_meet_photo": "",
    "site_videos_eyebrow": "Transformations", "site_videos_title": "Real clients.", "site_videos_gold": "Real results.",
    "site_videos_sub": "Watch haircuts and colour by RK and the team. Tap a video to play.",
    "site_services_eyebrow": "Services & pricing", "site_services_title": "Crafted for", "site_services_gold": "him, her & everyone",
    "site_reviews_eyebrow": "What clients say", "site_reviews_title": "Loved by", "site_reviews_gold": "South Mumbai",
    "site_team_eyebrow": "The team", "site_team_title": "Meet our", "site_team_gold": "stylists",
    "site_branches_eyebrow": "Find us", "site_branches_title": "Our", "site_branches_gold": "branches",
    "site_cta_title": "Ready for your", "site_cta_gold": "best look?", "site_cta_sub": "Book online in under a minute – or follow us for daily inspiration.",
    "site_show_meet": "1", "site_show_videos": "1", "site_show_services": "1", "site_show_reviews": "1", "site_show_team": "1", "site_show_branches": "1", "site_show_cta": "1",
    "whatsapp_mode": "simulated",
    "whatsapp_provider": "",
    "whatsapp_api_key": "",
    "whatsapp_sender": "",
}


# ----------------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------------
def setting(key, default=None):
    row = db.session.get(Setting, key)
    if row is not None and row.value is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key, default)


def setting_num(key, default=0):
    try:
        return float(setting(key, default))
    except (TypeError, ValueError):
        return float(default)


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = str(value)


def gst_on():
    return setting("gst_enabled") == "1"


# ----------------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------------
def inr(v, dec=None, sym=True):
    """Indian digit grouping: 1,23,456.50"""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0.0
    neg = v < 0
    v = abs(v)
    if dec is None:      # paise only where they matter (small amounts); big totals show whole rupees
        dec = 2 if abs(v - round(v)) > 0.004 and v < 10000 else 0
    s = f"{v:.{dec}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = head + "," + tail
    out = whole + ("." + frac if frac else "")
    return ("-" if neg else "") + ("₹" if sym else "") + out


def fmt_date(d, fmt="%d %b %Y"):
    if not d:
        return "—"
    return d.strftime(fmt)


def fmt_time(dt):
    if not dt:
        return "—"
    if isinstance(dt, str):
        h, m = dt.split(":")
        dt = datetime(2000, 1, 1, int(h), int(m))
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_dt(dt):
    return dt.strftime("%d %b, %I:%M %p").replace(" 0", " ") if dt else "—"


def hhmm_to_min(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def parse_date(s, default=None):
    if not s:
        return default
    for f in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return default


def to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def to_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def clean_mobile(m):
    return re.sub(r"\D", "", m or "")[-10:]


# ----------------------------------------------------------------------------
# periods (day / MTD / YTD ...)
# ----------------------------------------------------------------------------
def fy_start(d=None):
    d = d or date.today()
    m = int(setting_num("fy_start_month", 4))
    return date(d.year if d.month >= m else d.year - 1, m, 1)


def fy_label(d=None):
    s = fy_start(d)
    if s.month == 1:
        return str(s.year)
    return f"FY {s.year}-{str(s.year + 1)[2:]}"


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


PERIODS = [("today", "Today"), ("yesterday", "Yesterday"), ("week", "This week"), ("mtd", "Month to date"),
           ("lastmonth", "Last month"), ("ytd", "Year to date"), ("custom", "Custom")]


def period_range(key, start=None, end=None, today=None):
    today = today or date.today()
    if key == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if key == "week":
        return today - timedelta(days=today.weekday()), today
    if key == "mtd":
        return today.replace(day=1), today
    if key == "lastmonth":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if key == "ytd":
        return fy_start(today), today
    if key == "custom":
        s = parse_date(start, today.replace(day=1))
        e = parse_date(end, today)
        return (s, e) if s <= e else (e, s)
    return today, today


def get_period(default="mtd"):
    """Read ?p= &from= &to= from the request -> (key, d1, d2, label)."""
    key = request.args.get("p", default)
    if key not in dict(PERIODS):
        key = default
    d1, d2 = period_range(key, request.args.get("from"), request.args.get("to"))
    label = dict(PERIODS)[key]
    if d1 == d2:
        rng = fmt_date(d1)
    else:
        rng = f"{fmt_date(d1)} – {fmt_date(d2)}"
    return key, d1, d2, f"{label} · {rng}"


def daterange(d1, d2):
    d = d1
    while d <= d2:
        yield d
        d += timedelta(days=1)


# ----------------------------------------------------------------------------
# branch scope
# ----------------------------------------------------------------------------
def all_branches():
    return Branch.query.filter_by(active=True).order_by(Branch.id).all()


def scope_ids():
    """Branch ids the current user is looking at (branch users are locked to their branch)."""
    if current_user.is_authenticated and current_user.branch_id:
        return [current_user.branch_id]
    sel = session.get("branch_id", 0)
    if sel:
        return [int(sel)]
    return [b.id for b in all_branches()]


def active_branch():
    """The single branch in focus, or None when viewing all branches."""
    ids = scope_ids()
    if len(ids) == 1 and (current_user.branch_id or session.get("branch_id")):
        return db.session.get(Branch, ids[0])
    return None


def scope_label():
    b = active_branch()
    return b.name if b else "All Branches"


def form_branch_id(field="branch_id"):
    """Branch to write against: the user's own branch, else the chosen one, else the switcher selection."""
    if current_user.branch_id:
        return current_user.branch_id
    v = to_int(request.form.get(field)) or to_int(request.args.get(field))
    if v:
        return v
    sel = session.get("branch_id", 0)
    return int(sel) if sel else None


def can_access_branch(branch_id):
    if not branch_id:
        return current_user.is_super
    return current_user.is_super or current_user.branch_id == branch_id


def guard_branch(obj_branch_id):
    """Abort 403 if the current user may not touch a record from this branch."""
    if obj_branch_id and not can_access_branch(obj_branch_id):
        abort(403)


# ----------------------------------------------------------------------------
# permissions
# ----------------------------------------------------------------------------
def require_perm(*perms):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.path))
            if not any(current_user.has_perm(p) for p in perms):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def super_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        if not current_user.is_super:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def manager_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ----------------------------------------------------------------------------
# audit + notifications
# ----------------------------------------------------------------------------
def audit(action, entity, entity_id=None, detail="", branch_id=None):
    uid = current_user.id if has_request_context() and current_user.is_authenticated else None
    db.session.add(AuditLog(user_id=uid, action=action, entity=entity, entity_id=entity_id,
                            detail=(detail or "")[:300], branch_id=branch_id))


def notify(employee_id, title, body="", link=None, kind="info"):
    if employee_id:
        db.session.add(Notification(employee_id=employee_id, title=title, body=body, link=link, kind=kind))


def notify_branch_managers(branch_id, title, body="", link=None, kind="info", include_super=False):
    q = Employee.query.filter(Employee.active == True)  # noqa: E712
    ids = [e.id for e in q.filter(Employee.role == "branch_admin", Employee.branch_id == branch_id)]
    if include_super:
        ids += [e.id for e in q.filter(Employee.role == "super_admin")]
    for i in ids:
        notify(i, title, body, link, kind)
    return ids


def notify_supers(title, body="", link=None, kind="info"):
    for e in Employee.query.filter_by(role="super_admin", active=True):
        notify(e.id, title, body, link, kind)


def token(n=10):
    return secrets.token_urlsafe(n)[:n + 4]


def flash_ok(msg):
    flash(msg, "ok")


def flash_err(msg):
    flash(msg, "err")
