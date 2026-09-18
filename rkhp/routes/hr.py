import base64
import os
import uuid
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from ..extensions import db
from ..helpers import (all_branches, audit, clean_mobile, daterange, guard_branch, manager_only, month_bounds, notify, notify_supers, parse_date, require_perm,
                       scope_ids, setting, setting_num, super_only, to_float, to_int)
from ..logic import finance as fin
from ..logic import payroll as pr
from ..logic import whatsapp as wa
from ..models import (ALL_PERMS, PERMS, ROLE_RANK, ROLES, Announcement, Attendance, Branch, Category, Employee, EmployeeDayBranch, Holiday, LeaveRequest,
                      Payroll, Reimbursement, RoleRequest)

bp = Blueprint("hr", __name__, url_prefix="/hr")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _save_upload(field, sub="docs"):
    f = request.files.get(field)
    if not f or not f.filename:
        return None
    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".pdf", ".webp"):
        return None
    name = f"{sub}_{uuid.uuid4().hex[:10]}{ext}"
    f.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


@bp.route("/files/<name>")
def files(name):
    if not current_user.is_authenticated:
        abort(403)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], name)


def _emp_scope(include_super=False):
    q = Employee.query
    if not include_super:
        q = q.filter(Employee.role != "super_admin")
    ids = scope_ids()
    if current_user.is_super and include_super:
        return q
    return q.filter(Employee.branch_id.in_(ids))


# ============================== employees ==============================
@bp.route("/")
@require_perm("hr")
def employees():
    show = request.args.get("show", "active")
    q = Employee.query.filter((Employee.branch_id.in_(scope_ids())) | (Employee.role == "super_admin") if current_user.is_super else Employee.branch_id.in_(scope_ids()))
    if show == "active":
        q = q.filter(Employee.active == True)  # noqa: E712
    elif show == "inactive":
        q = q.filter(Employee.active == False)  # noqa: E712
    rows = q.order_by(Employee.role != "super_admin", Employee.branch_id, Employee.name).all()
    rows.sort(key=lambda e: (ROLE_RANK.get(e.role, 9), e.branch_id or 0, e.name))
    return render_template("hr/employees.html", rows=rows, show=show, page_title="Employees")


@bp.route("/org")
@require_perm("hr")
def org():
    q = Employee.query.filter_by(active=True)
    ids = scope_ids()
    emps = [e for e in q.all() if e.role == "super_admin" or e.branch_id in ids]
    kids = {}
    for e in emps:
        kids.setdefault(e.reports_to_id, []).append(e)
    roots = [e for e in emps if e.reports_to_id not in {x.id for x in emps}]
    roots.sort(key=lambda e: ROLE_RANK.get(e.role, 9))
    return render_template("hr/org.html", roots=roots, kids=kids, page_title="Organisation chart")


@bp.route("/employee/new", methods=["GET", "POST"])
@bp.route("/employee/<int:eid>/edit", methods=["GET", "POST"])
@require_perm("hr")
def employee_form(eid=None):
    e = db.session.get(Employee, eid) if eid else None
    if eid and not e:
        abort(404)
    if e and e.role == "super_admin" and not current_user.is_super:
        abort(403)
    if e:
        guard_branch(e.branch_id or e.home_branch_id)
    if request.method == "POST":
        f = request.form
        mobile = clean_mobile(f.get("mobile"))
        if len(mobile) != 10:
            flash("Mobile number (login ID) must be 10 digits.", "err")
        elif Employee.query.filter(Employee.mobile == mobile, Employee.id != (e.id if e else 0)).first():
            flash("Another employee already uses this mobile number.", "err")
        else:
            new = e is None
            if new:
                e = Employee(mobile=mobile, salary=setting_num("default_salary", 18000), commission_pct=setting_num("default_commission", 10))
                db.session.add(e)
                e.set_password(f.get("password") or "Gold@123")
            e.name, e.mobile = f["name"].strip(), mobile
            bid = current_user.branch_id or to_int(f.get("branch_id"))
            if new or current_user.is_super:
                e.branch_id = bid if (f.get("role") != "super_admin") else None
                e.home_branch_id = to_int(f.get("home_branch_id")) or bid
            e.designation, e.email, e.address = f.get("designation"), f.get("email"), f.get("address")
            e.dob, e.joined_on = parse_date(f.get("dob")), parse_date(f.get("joined_on"), date.today())
            e.aadhaar, e.pan = f.get("aadhaar"), f.get("pan")
            e.emergency_name, e.emergency_phone = f.get("emergency_name"), f.get("emergency_phone")
            e.bank_name, e.bank_account, e.ifsc, e.upi_id = f.get("bank_name"), f.get("bank_account"), f.get("ifsc"), f.get("upi_id")
            e.weekly_off = to_int(f.get("weekly_off"), 0)
            e.shift_start, e.shift_end = f.get("shift_start") or "10:00", f.get("shift_end") or "21:00"
            e.tip_mode = f.get("tip_mode") or "daily"
            e.bio = f.get("bio")
            e.reports_to_id = to_int(f.get("reports_to_id")) or None
            if current_user.is_super:                       # salary, role, permissions: Super Admin only
                e.role = f.get("role") or e.role or "stylist"
                e.salary = to_float(f.get("salary"), e.salary)
                e.commission_pct = to_float(f.get("commission_pct"), e.commission_pct)
                e.monthly_target = to_float(f.get("monthly_target"), e.monthly_target or 0)
                e.takes_appointments = f.get("takes_appointments") == "1"
                e.public_visible = f.get("public_visible") == "1"
                e.perms = ",".join(p for p in ALL_PERMS if f.get(f"perm_{p}") == "1")
            elif new:
                e.role = "stylist"
            for fld, col in (("aadhaar_doc", "aadhaar_doc"), ("pan_doc", "pan_doc"), ("photo_file", "photo"), ("face_file", "face_photo")):
                nm = _save_upload(fld)
                if nm:
                    setattr(e, col, nm)
            audit("create" if new else "update", "employee", e.id, e.name, e.branch_id)
            db.session.commit()
            flash(f"Employee <b>{e.name}</b> saved." + (f" Login: mobile {e.mobile}, password as set." if new else ""), "ok")
            return redirect(url_for("hr.employee_view", eid=e.id))
    mgrs = Employee.query.filter(Employee.active == True, Employee.role.in_(("super_admin", "branch_admin", "senior_stylist", "stylist"))).order_by(Employee.name).all()  # noqa: E712
    return render_template("hr/employee_form.html", e=e, roles=ROLES, perms=PERMS, mgrs=mgrs, weekdays=WEEKDAYS, page_title="Edit employee" if e else "Add employee", f=request.form)


@bp.route("/employee/<int:eid>")
@require_perm("hr")
def employee_view(eid):
    e = db.session.get(Employee, eid) or abort(404)
    guard_branch(e.branch_id or e.home_branch_id)
    today = date.today()
    m1 = today.replace(day=1)
    s = pr.attendance_summary(e, m1, today)
    rev = pr.service_revenue(e.id, m1, today)
    tips = fin.tip_balance(e.id)
    overrides = EmployeeDayBranch.query.filter(EmployeeDayBranch.employee_id == e.id, EmployeeDayBranch.date >= today).order_by(EmployeeDayBranch.date).all()
    reqs = RoleRequest.query.filter_by(employee_id=e.id).order_by(RoleRequest.created_at.desc()).limit(5).all()
    recent = Attendance.query.filter_by(employee_id=e.id).order_by(Attendance.date.desc()).limit(10).all()
    return render_template("hr/employee_view.html", e=e, s=s, rev=rev, tips=tips, overrides=overrides, reqs=reqs, recent=recent, roles=ROLES, weekdays=WEEKDAYS, branches=all_branches(),
                           page_title=e.name, perms=PERMS, today=today)


@bp.route("/employee/<int:eid>/action", methods=["POST"])
@require_perm("hr")
def employee_action(eid):
    e = db.session.get(Employee, eid) or abort(404)
    guard_branch(e.branch_id or e.home_branch_id)
    act = request.form.get("action")
    if e.role == "super_admin" and act in ("block", "unblock", "deactivate"):
        flash("The Super Admin account cannot be blocked or removed.", "err")
        return redirect(url_for("hr.employee_view", eid=eid))
    if act == "block":
        e.login_blocked = True; flash(f"{e.name}'s app access is blocked.", "ok")
    elif act == "unblock":
        e.login_blocked = False; flash(f"{e.name}'s app access is restored.", "ok")
    elif act == "deactivate":
        e.active = False; flash(f"{e.name} marked as ex-employee. History is preserved.", "ok")
    elif act == "reactivate":
        e.active = True; flash(f"{e.name} reactivated.", "ok")
    elif act == "reset":
        e.set_password(request.form.get("password") or "Gold@123"); flash("Password reset.", "ok")
    elif act == "delete":
        if not current_user.is_super:
            abort(403)
        from ..models import Invoice, Transaction
        if Invoice.query.filter_by(created_by_id=e.id).first() or Transaction.query.filter_by(employee_id=e.id).first() or Attendance.query.filter_by(employee_id=e.id).first():
            flash("This employee has business history, so they can't be deleted – use 'Mark as ex-employee' instead.", "err")
            return redirect(url_for("hr.employee_view", eid=eid))
        db.session.delete(e); db.session.commit(); flash("Employee deleted.", "ok")
        return redirect(url_for("hr.employees"))
    audit(act, "employee", e.id, e.name, e.branch_id)
    db.session.commit()
    return redirect(url_for("hr.employee_view", eid=eid))


@bp.route("/role-request", methods=["POST"])
@manager_only
def role_request():
    e = db.session.get(Employee, to_int(request.form.get("employee_id"))) or abort(404)
    guard_branch(e.branch_id)
    to = request.form.get("to_role")
    if current_user.is_super:
        e.role = to
        db.session.commit()
        flash(f"{e.name} is now {dict(ROLES)[to]}.", "ok")
    else:
        db.session.add(RoleRequest(employee_id=e.id, from_role=e.role, to_role=to, reason=request.form.get("reason"), requested_by_id=current_user.id))
        notify_supers("Role change request", f"{current_user.name} requests {e.name}: {dict(ROLES)[e.role]} → {dict(ROLES)[to]}", "/approvals", "approval")
        db.session.commit()
        flash("Request sent to the Super Admin for approval.", "ok")
    return redirect(url_for("hr.employee_view", eid=e.id))


@bp.route("/role-request/<int:rid>/<act>", methods=["POST"])
@super_only
def role_decide(rid, act):
    r = db.session.get(RoleRequest, rid) or abort(404)
    r.status = "approved" if act == "approve" else "rejected"
    r.decided_by_id, r.decided_at = current_user.id, datetime.now()
    if act == "approve":
        r.employee.role = r.to_role
    wa.simple("role_decision", r.employee, employee=r.employee.name, from_role=dict(ROLES)[r.from_role], to_role=dict(ROLES)[r.to_role], status=r.status)
    notify(r.requested_by_id, f"Role request {r.status}", f"{r.employee.name}: {dict(ROLES)[r.from_role]} → {dict(ROLES)[r.to_role]}", None, "approval")
    db.session.commit()
    flash(f"Role change {r.status}.", "ok")
    return redirect(request.referrer or url_for("misc.approvals"))


# ============================== attendance ==============================
@bp.route("/attendance")
@require_perm("hr")
def attendance():
    ym = request.args.get("month") or date.today().strftime("%Y-%m")
    d1, d2 = month_bounds(ym)
    ids = scope_ids()
    emps = Employee.query.filter(Employee.active == True, Employee.role != "super_admin", Employee.branch_id.in_(ids)).order_by(Employee.branch_id, Employee.name).all()  # noqa: E712
    today_rows = {a.employee_id: a for a in Attendance.query.filter_by(date=date.today())}
    sheet = [(e, pr.attendance_summary(e, d1, d2)) for e in emps]
    days = list(daterange(d1, d2))
    return render_template("hr/attendance.html", sheet=sheet, days=days, ym=ym, today_rows=today_rows, page_title="Attendance", today=date.today())


@bp.route("/attendance/mark", methods=["POST"])
@require_perm("hr")
def attendance_mark():
    e = db.session.get(Employee, to_int(request.form.get("employee_id"))) or abort(404)
    guard_branch(e.branch_id)
    d = parse_date(request.form.get("date"))
    st = request.form.get("status")
    a = Attendance.query.filter_by(employee_id=e.id, date=d).first()
    if st == "clear":
        if a:
            db.session.delete(a)
    else:
        a = a or Attendance(employee_id=e.id, date=d)
        db.session.add(a)
        a.method, a.note = "manual", request.form.get("note") or "Marked by manager"
        if st in ("present", "half_day"):
            ci = request.form.get("check_in") or e.shift_start
            co = request.form.get("check_out") or e.shift_end
            a.check_in = datetime.combine(d, datetime.strptime(ci, "%H:%M").time())
            a.check_out = datetime.combine(d, datetime.strptime(co, "%H:%M").time())
            pr.set_out_metrics(a, e)
            late = a.check_in.hour * 60 + a.check_in.minute - (int(e.shift_start[:2]) * 60 + int(e.shift_start[3:]))
            a.late_min = late if late > setting_num("grace_minutes", 15) else 0
            a.status = st
        else:
            a.status, a.check_in, a.check_out, a.late_min, a.early_min, a.worked_min = st, None, None, 0, 0, 0
    audit("attendance", "employee", e.id, f"{d} {st}", e.branch_id)
    db.session.commit()
    flash("Attendance updated.", "ok")
    return redirect(request.referrer or url_for("hr.attendance"))


@bp.route("/kiosk")
@require_perm("hr")
def kiosk():
    ids = scope_ids()
    emps = Employee.query.filter(Employee.active == True, Employee.role != "super_admin", Employee.branch_id.in_(ids)).order_by(Employee.name).all()  # noqa: E712
    today = {a.employee_id: a for a in Attendance.query.filter_by(date=date.today())}
    return render_template("hr/kiosk.html", emps=emps, today=today, page_title="Face attendance kiosk")


@bp.route("/kiosk/scan", methods=["POST"])
@require_perm("hr")
def kiosk_scan():
    """DEMO: the face match is simulated - the employee chosen on screen is treated as 'recognised'.
    Swap this function's matching step for a real face-recognition engine to go live."""
    e = db.session.get(Employee, to_int(request.form.get("employee_id")))
    if not e or e.branch_id not in scope_ids():
        return jsonify(ok=False, message="Face not recognised. Please try again."), 404
    snap = None
    data = request.form.get("snapshot", "")
    if data.startswith("data:image") and "," in data:
        try:
            snap = f"snap_{e.id}_{datetime.now():%Y%m%d%H%M%S}.jpg"
            with open(os.path.join(current_app.config["UPLOAD_FOLDER"], snap), "wb") as fh:
                fh.write(base64.b64decode(data.split(",", 1)[1]))
        except Exception:
            snap = None
    a, action = pr.record_scan(e, datetime.now(), "face", snap)
    db.session.commit()
    if action == "dup":
        return jsonify(ok=True, action="dup", message=f"{e.name} – already scanned a moment ago.", time=a.check_in.strftime("%I:%M %p"))
    msg = f"Welcome {e.name.split()[0]}! Checked IN at {a.check_in.strftime('%I:%M %p')}" + (f" · LATE by {a.late_min} min" if a.late_min else " · on time ✔")
    if action == "out":
        msg = f"Goodbye {e.name.split()[0]}! Checked OUT at {a.check_out.strftime('%I:%M %p')} · worked {a.worked_min // 60}h {a.worked_min % 60}m" + (f" · EARLY exit by {a.early_min} min" if a.early_min else "")
    return jsonify(ok=True, action=action, message=msg, late=a.late_min, early=a.early_min)


# ============================== leaves ==============================
@bp.route("/leaves")
@require_perm("hr")
def leaves():
    ids = scope_ids()
    rows = LeaveRequest.query.join(Employee, Employee.id == LeaveRequest.employee_id).filter(Employee.branch_id.in_(ids)).order_by(LeaveRequest.status == "pending", LeaveRequest.created_at.desc()).limit(200).all()
    rows.sort(key=lambda r: (r.status != "pending", -r.created_at.timestamp()))
    return render_template("hr/leaves.html", rows=rows, page_title="Leave requests")


@bp.route("/leaves/<int:lid>/<act>", methods=["POST"])
@manager_only
def leave_decide(lid, act):
    r = db.session.get(LeaveRequest, lid) or abort(404)
    guard_branch(r.employee.branch_id)
    r.status = "approved" if act == "approve" else "rejected"
    r.decided_by_id, r.decided_at = current_user.id, datetime.now()
    if act == "approve":       # mark attendance as leave so payroll picks it up
        for d in daterange(r.from_date, r.to_date):
            a = Attendance.query.filter_by(employee_id=r.employee_id, date=d).first()
            if not a and d.weekday() != r.employee.weekly_off:
                db.session.add(Attendance(employee_id=r.employee_id, date=d, status="leave", method="manual", note="Approved leave"))
    dates = r.from_date.strftime("%d %b") + ("" if r.days == 1 else " – " + r.to_date.strftime("%d %b"))
    wa.simple("leave_decision", r.employee, employee=r.employee.name, dates=dates, status=r.status, extra="")
    notify(r.employee_id, f"Leave {r.status}", dates, "/me/leave", "approval")
    db.session.commit()
    flash(f"Leave {r.status}.", "ok")
    return redirect(request.referrer or url_for("hr.leaves"))


# ============================== payroll ==============================
@bp.route("/payroll")
@require_perm("hr")
def payroll():
    ym = request.args.get("month") or date.today().strftime("%Y-%m")
    ids = scope_ids()
    pays = pr.generate_payroll(ym, ids)
    db.session.commit()
    accounts = fin.scope_accounts(ids)
    return render_template("hr/payroll.html", pays=pays, ym=ym, accounts=accounts, page_title="Payroll", MODE=fin.MODE_LABEL,
                           tot=dict(net=sum(p.net_salary for p in pays), tips=sum(p.tips_amt for p in pays), pending=sum(p.total_payable for p in pays if p.status != "paid"), inc=sum(p.incentive_amt for p in pays)))


@bp.route("/payroll/<int:pid>/pay", methods=["POST"])
@require_perm("hr", "finance")
def payroll_pay(pid):
    p = db.session.get(Payroll, pid) or abort(404)
    guard_branch(p.branch_id)
    if p.status == "paid":
        flash("Already paid.", "err")
    else:
        pr.pay_payroll(p, request.form.get("mode", "bank"), to_int(request.form.get("account_id")), parse_date(request.form.get("date"), date.today()), current_user.id)
        audit("pay", "payroll", p.id, f"{p.employee.name} {p.month}", p.branch_id)
        db.session.commit()
        flash(f"Salary paid to <b>{p.employee.name}</b>. <span class='badge'>WhatsApp + in-app notification sent (demo)</span>", "ok")
    return redirect(request.referrer or url_for("hr.payroll", month=p.month))


@bp.route("/payslip/<int:pid>")
def payslip(pid):
    p = db.session.get(Payroll, pid) or abort(404)
    if not current_user.is_authenticated or (current_user.id != p.employee_id and not current_user.has_perm("hr")):
        abort(403)
    if current_user.id != p.employee_id:
        guard_branch(p.branch_id)
    return render_template("hr/payslip.html", p=p, page_title=f"Payslip {p.month}", MODE=fin.MODE_LABEL)


# ============================== reimbursements ==============================
@bp.route("/reimbursements")
@require_perm("hr", "finance")
def reimbursements():
    ids = scope_ids()
    rows = Reimbursement.query.filter(Reimbursement.branch_id.in_(ids)).order_by(Reimbursement.created_at.desc()).limit(200).all()
    rows.sort(key=lambda r: (r.status != "pending", r.status != "approved"))
    return render_template("hr/reimbursements.html", rows=rows, accounts=fin.scope_accounts(ids), page_title="Reimbursements")


@bp.route("/reimbursements/<int:rid>/<act>", methods=["POST"])
@manager_only
def reimb_decide(rid, act):
    r = db.session.get(Reimbursement, rid) or abort(404)
    guard_branch(r.branch_id)
    if act == "reject":
        r.status = "rejected"
        wa.simple("reimb_decision", r.employee, employee=r.employee.name, amount=fin.inr(r.amount), description=r.description, status="rejected", extra=" Please speak to your manager.")
    elif act in ("approve", "pay"):
        r.status, r.decided_by_id = "approved", current_user.id
        if act == "pay":
            from ..logic import payroll as _pr
            _pr.pay_reimbursement(r, request.form.get("mode", "cash"), to_int(request.form.get("account_id")), date.today(), current_user.id)
        else:
            wa.simple("reimb_decision", r.employee, employee=r.employee.name, amount=fin.inr(r.amount), description=r.description, status="approved",
                      extra=" It will be paid " + ("today." if r.settle_mode == "daily" else "with your salary."))
    notify(r.employee_id, f"Reimbursement {r.status}", f"{fin.inr(r.amount)} – {r.description}", "/me/reimbursements", "approval")
    db.session.commit()
    flash(f"Reimbursement {r.status}.", "ok")
    return redirect(request.referrer or url_for("hr.reimbursements"))


# ============================== announcements ==============================
@bp.route("/announcements", methods=["GET", "POST"])
@require_perm("hr")
def announcements():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "delete":
            a = db.session.get(Announcement, to_int(request.form.get("id")))
            if a:
                db.session.delete(a)
        else:
            a = Announcement(title=request.form["title"].strip(), body=request.form.get("body"), pinned=request.form.get("pinned") == "1", created_by_id=current_user.id,
                             branch_id=current_user.branch_id or to_int(request.form.get("branch_id")) or None)
            db.session.add(a)
            db.session.flush()
            n = 0
            q = Employee.query.filter(Employee.active == True, Employee.role != "super_admin")  # noqa: E712
            if a.branch_id:
                q = q.filter(Employee.branch_id == a.branch_id)
            for e in q:
                notify(e.id, "📢 " + a.title, (a.body or "")[:120], "/me/announcements", "announce")
                n += 1
                if request.form.get("whatsapp") == "1":
                    wa.simple("announcement", e, title=a.title, body=a.body or "")
        db.session.commit()
        flash("Announcement posted to the employee app." + (" WhatsApp broadcast queued (demo)." if request.form.get("whatsapp") == "1" else ""), "ok")
        return redirect(url_for("hr.announcements"))
    q = Announcement.query
    if not current_user.is_super:
        q = q.filter((Announcement.branch_id == current_user.branch_id) | (Announcement.branch_id.is_(None)))
    return render_template("hr/announcements.html", rows=q.order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all(), page_title="Announcements")
