from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, logout_user

from ..extensions import db
from ..helpers import audit, require_perm, set_setting, setting, super_only, to_float, to_int
from ..logic import whatsapp as wa
from ..models import Branch, Employee
from .. import seed

bp = Blueprint("sett", __name__, url_prefix="/settings")

TAB_KEYS = {
    "business": ["business_name", "tagline", "owner_name", "google_review_link", "gstin"],
    "contact": ["phone_central", "whatsapp_number", "wa_greeting", "instagram", "facebook", "youtube", "email", "website"],
    "finance": ["gst_enabled", "gst_rate", "fy_start_month", "loyalty_earn_per"],
    "hr": ["default_salary", "default_commission", "grace_minutes", "late_marks_per_halfday", "early_exits_per_halfday", "half_day_min_hours", "full_day_min_hours", "paid_leaves_per_month", "salary_days_basis"],
    "booking": ["booking_advance_days", "booking_min_notice_min", "reminder_minutes"],
    "whatsapp": ["whatsapp_mode", "whatsapp_provider", "whatsapp_api_key", "whatsapp_sender"],
}


@bp.route("/")
@require_perm("settings")
def index():
    tab = request.args.get("tab", "business")
    vals = {k: setting(k) for ks in TAB_KEYS.values() for k in ks}
    tpls = [(k, v[0], wa.template_text(k)) for k, v in wa.TEMPLATES.items()] if tab == "templates" else []
    return render_template("settings/index.html", tab=tab, v=vals, branches=Branch.query.order_by(Branch.id).all(), tpls=tpls, page_title="Settings")


@bp.route("/save", methods=["POST"])
@super_only
def save():
    tab = request.form.get("tab", "business")
    for k in TAB_KEYS.get(tab, []):
        if tab == "finance" and k == "gst_enabled":
            set_setting(k, "1" if request.form.get(k) == "1" else "0")
        elif k in request.form:
            set_setting(k, request.form.get(k, "").strip())
    audit("settings", "settings", None, tab)
    db.session.commit()
    flash("Settings saved.", "ok")
    return redirect(url_for("sett.index", tab=tab))


@bp.route("/templates", methods=["POST"])
@super_only
def templates():
    for k in wa.TEMPLATES:
        v = request.form.get("tpl_" + k)
        if v is not None:
            if v.strip() and v.strip() != wa.TEMPLATES[k][1].strip():
                set_setting("tpl_" + k, v)
            else:
                set_setting("tpl_" + k, "")
    db.session.commit()
    flash("Message templates saved.", "ok")
    return redirect(url_for("sett.index", tab="templates"))


@bp.route("/branch", methods=["POST"])
@super_only
def branch():
    f = request.form
    new = not f.get("id")
    b = Branch() if new else (db.session.get(Branch, to_int(f.get("id"))) or abort(404))
    b.name, b.code, b.address, b.phone = f["name"].strip(), f["code"].strip().upper()[:6], f.get("address"), f.get("phone")
    b.open_time, b.close_time, b.slot_minutes = f.get("open_time") or "10:00", f.get("close_time") or "21:00", to_int(f.get("slot_minutes"), 30)
    b.monthly_target = to_float(f.get("monthly_target"))
    if not new:
        b.active = f.get("active", "1") == "1"
    else:
        db.session.add(b)
        db.session.flush()
        # a new branch is ready to trade immediately: cash counter, card machine, UPI account, stock levels
        from ..models import Account, Item, StockLevel
        for t, nm in (("cash", "Cash Counter"), ("card", "Card Machine (settlement)"), ("upi", "Salon UPI")):
            db.session.add(Account(name=f"{b.name} – {nm}", type=t, branch_id=b.id, opening_balance=0))
        for it in Item.query.all():
            db.session.add(StockLevel(item_id=it.id, branch_id=b.id, qty=0, reorder_level=0))
    audit("save", "branch", b.id, b.name)
    db.session.commit()
    flash(f"Branch <b>{b.name}</b> " + ("added. Cash counter, card and UPI accounts and stock levels were created automatically – now add its staff in HR." if new else "updated."), "ok")
    return redirect(url_for("sett.index", tab="branches"))


@bp.route("/reset-demo", methods=["POST"])
@super_only
def reset_demo():
    if request.form.get("confirm", "").strip().upper() != "RESET":
        flash("Type RESET to confirm.", "err")
        return redirect(url_for("sett.index", tab="data"))
    mode = request.form.get("mode", "demo")
    seed.reset_demo() if mode == "demo" else seed.reset_clean()
    logout_user()
    flash("Data has been reset. Please sign in again.", "ok")
    return redirect(url_for("auth.login"))
