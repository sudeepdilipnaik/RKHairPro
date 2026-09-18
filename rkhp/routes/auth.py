from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from ..extensions import db
from ..helpers import clean_mobile, to_int
from ..models import Employee

bp = Blueprint("auth", __name__)


def _home_for(user):
    return url_for("dash.index") if user.perm_set else url_for("me.home")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Signed-in users can still open this page to switch to another account (useful for demos).
    already = current_user if current_user.is_authenticated else None
    if request.method == "POST":
        mobile = clean_mobile(request.form.get("mobile"))
        u = Employee.query.filter_by(mobile=mobile).first()
        if not u or not u.check_password(request.form.get("password", "")):
            flash("Mobile number or password is not correct.", "err")
        elif u.login_blocked:
            flash("Your access has been blocked by the admin. Please contact your manager.", "err")
        elif not u.active:
            flash("This account is no longer active.", "err")
        else:
            login_user(u, remember=True)
            session["branch_id"] = 0
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else _home_for(u))
    demo = []
    if current_app.config.get("DEMO_MODE"):
        for m in ("9820000001", "9820000011", "9820000021", "9820000012", "9820000013", "9820000015"):
            e = Employee.query.filter_by(mobile=m).first()
            if e:
                demo.append(e)
    if current_app.config.get("DEMO_MODE"):
        for m in ("9820000014", "9820000016", "9820000023", "9820000022"):      # a few more employees to try
            e = Employee.query.filter_by(mobile=m).first()
            if e:
                demo.append(e)
    return render_template("login.html", demo=demo, already=already, page_title="Sign in", pw=current_app.config["DEFAULT_PASSWORD"])


@bp.route("/demo-login/<int:eid>", methods=["POST"])
def demo_login(eid):
    if not current_app.config.get("DEMO_MODE"):
        return redirect(url_for("auth.login"))
    u = db.session.get(Employee, eid)
    if u and u.is_active:
        login_user(u, remember=True)
        session["branch_id"] = 0
        return redirect(_home_for(u))
    return redirect(url_for("auth.login"))


@bp.route("/logout")
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/switch-branch", methods=["POST"])
def switch_branch():
    if current_user.is_authenticated and current_user.is_super:
        session["branch_id"] = to_int(request.form.get("branch_id"), 0) or 0
    return redirect(request.referrer or url_for("dash.index"))
