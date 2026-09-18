"""Employee self-service portal (mobile friendly)."""
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import daterange, fy_start, month_bounds, notify_branch_managers, parse_date, to_float, to_int
from ..logic import appointments as al
from ..logic import finance as fin
from ..logic import payroll as pr
from ..models import (Announcement, Appointment, Attendance, Category, Feedback, Invoice, InvoiceItem, InvoiceTip, LeaveRequest, Notification, Payroll,
                      Reimbursement)

bp = Blueprint("me", __name__, url_prefix="/me")


@bp.route("/")
@login_required
def home():
    u = current_user
    today = date.today()
    att = Attendance.query.filter_by(employee_id=u.id, date=today).first()
    m1 = today.replace(day=1)
    s = pr.attendance_summary(u, m1, today)
    rev_day, rev_mtd = pr.service_revenue(u.id, today, today), pr.service_revenue(u.id, m1, today)
    tips_bal = fin.tip_balance(u.id)
    tips_today = float(db.session.query(db.func.coalesce(db.func.sum(InvoiceTip.amount), 0)).join(Invoice, Invoice.id == InvoiceTip.invoice_id)
                       .filter(InvoiceTip.employee_id == u.id, Invoice.date == today, Invoice.status != "void").scalar() or 0)
    start = datetime.combine(today, datetime.min.time())
    my_appts = Appointment.query.filter(Appointment.employee_id == u.id, Appointment.start_dt >= start, Appointment.start_dt < start + timedelta(days=1)).order_by(Appointment.start_dt).all()
    later = Appointment.query.filter(Appointment.employee_id == u.id, Appointment.start_dt >= start + timedelta(days=1), Appointment.start_dt < start + timedelta(days=8),
                                     Appointment.status.in_(("booked", "confirmed"))).order_by(Appointment.start_dt).limit(8).all()
    news = Announcement.query.filter((Announcement.branch_id == u.branch_id) | (Announcement.branch_id.is_(None))).order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).limit(3).all()
    inc = round(rev_mtd * (u.commission_pct or 0) / 100, 2)
    perf = []
    for label, a, b in (("Today", today, today), ("Month to date", m1, today), ("Year to date", fy_start(today), today)):
        rev = pr.service_revenue(u.id, a, b)
        bills = (db.session.query(db.func.count(db.func.distinct(InvoiceItem.invoice_id))).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                 .filter(InvoiceItem.employee_id == u.id, InvoiceItem.kind == "service", Invoice.status != "void", Invoice.date.between(a, b)).scalar() or 0)
        tips = float(db.session.query(db.func.coalesce(db.func.sum(InvoiceTip.amount), 0)).join(Invoice, Invoice.id == InvoiceTip.invoice_id)
                     .filter(InvoiceTip.employee_id == u.id, Invoice.status != "void", Invoice.date.between(a, b)).scalar() or 0)
        rating = (db.session.query(db.func.avg(Feedback.rating)).filter(Feedback.employee_id == u.id, Feedback.rating.isnot(None),
                                                                       db.func.date(Feedback.submitted_at).between(a, b)).scalar())
        perf.append(dict(label=label, rev=rev, bills=bills, avg=round(rev / bills) if bills else 0, tips=tips, inc=round(rev * (u.commission_pct or 0) / 100, 2), rating=round(rating, 1) if rating else None))
    return render_template("portal/home.html", att=att, s=s, rev_day=rev_day, rev_mtd=rev_mtd, tips_bal=tips_bal, tips_today=tips_today, my_appts=my_appts, later=later, news=news,
                           inc=inc, today=today, perf=perf, page_title="My day")


@bp.route("/attendance")
@login_required
def attendance():
    ym = request.args.get("month") or date.today().strftime("%Y-%m")
    d1, d2 = month_bounds(ym)
    s = pr.attendance_summary(current_user, d1, d2)
    recs = {a.date: a for a in Attendance.query.filter(Attendance.employee_id == current_user.id, Attendance.date.between(d1, d2))}
    return render_template("portal/attendance.html", s=s, recs=recs, ym=ym, days=list(daterange(d1, d2)), page_title="My attendance")


@bp.route("/earnings")
@login_required
def earnings():
    u = current_user
    today = date.today()
    m1 = today.replace(day=1)
    rev = pr.service_revenue(u.id, m1, today)
    tips_bal = fin.tip_balance(u.id)
    tips = fin.tips_table([u.branch_id or u.home_branch_id], today.replace(day=1), today)
    my_tips = next((r for r in tips if r["emp"].id == u.id), None)
    tip_rows = (db.session.query(Invoice.date, InvoiceTip.amount, Invoice.number).join(Invoice, Invoice.id == InvoiceTip.invoice_id)
                .filter(InvoiceTip.employee_id == u.id, Invoice.status != "void").order_by(Invoice.date.desc(), Invoice.id.desc()).limit(25).all())
    from ..models import Transaction
    payouts = Transaction.query.join(Category, Category.id == Transaction.category_id).filter(Transaction.employee_id == u.id, Category.nature == "tip_out").order_by(Transaction.date.desc()).limit(15).all()
    slips = Payroll.query.filter_by(employee_id=u.id).order_by(Payroll.month.desc()).limit(12).all()
    cur = pr.compute_payroll(u, today.strftime("%Y-%m"))
    return render_template("portal/earnings.html", rev=rev, inc=round(rev * (u.commission_pct or 0) / 100, 2), tips_bal=tips_bal, my_tips=my_tips, tip_rows=tip_rows, payouts=payouts,
                           slips=slips, cur=cur, page_title="Earnings & tips")


@bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    if request.method == "POST":
        f, t = parse_date(request.form.get("from")), parse_date(request.form.get("to"))
        if not f or not t or t < f or f < date.today() - timedelta(days=7):
            flash("Please choose valid dates.", "err")
        else:
            db.session.add(LeaveRequest(employee_id=current_user.id, from_date=f, to_date=t, kind=request.form.get("kind", "paid"), reason=request.form.get("reason")))
            notify_branch_managers(current_user.branch_id, "Leave request", f"{current_user.name}: {f:%d %b} – {t:%d %b}", "/approvals", "approval")
            db.session.commit()
            flash("Leave request sent to your manager.", "ok")
        return redirect(url_for("me.leave"))
    rows = LeaveRequest.query.filter_by(employee_id=current_user.id).order_by(LeaveRequest.created_at.desc()).all()
    return render_template("portal/leave.html", rows=rows, page_title="Leave", today=date.today())


@bp.route("/reimbursements", methods=["GET", "POST"])
@login_required
def reimb():
    if request.method == "POST":
        amt = to_float(request.form.get("amount"))
        if amt <= 0 or not request.form.get("description"):
            flash("Enter the amount and what it was for.", "err")
        else:
            r = Reimbursement(employee_id=current_user.id, branch_id=current_user.branch_id or current_user.home_branch_id, amount=amt, description=request.form["description"].strip(),
                              category_id=to_int(request.form.get("category_id")), settle_mode=request.form.get("settle_mode", "salary"), date=parse_date(request.form.get("date"), date.today()))
            from .hr import _save_upload
            r.receipt = _save_upload("receipt", "rcpt")
            db.session.add(r)
            notify_branch_managers(r.branch_id, "Reimbursement request", f"{current_user.name}: ₹{amt:,.0f} – {r.description}", "/approvals", "approval")
            db.session.commit()
            flash("Request raised. Your manager will approve it and it will be paid " + ("today." if r.settle_mode == "daily" else "with your salary."), "ok")
        return redirect(url_for("me.reimb"))
    rows = Reimbursement.query.filter_by(employee_id=current_user.id).order_by(Reimbursement.created_at.desc()).all()
    heads = Category.query.filter_by(kind="expense", nature="expense", active=True).order_by(Category.name).all()
    return render_template("portal/reimb.html", rows=rows, heads=heads, today=date.today(), page_title="Reimbursements")


@bp.route("/announcements")
@login_required
def announcements():
    u = current_user
    rows = Announcement.query.filter((Announcement.branch_id == u.branch_id) | (Announcement.branch_id.is_(None))).order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all()
    return render_template("portal/announcements.html", rows=rows, page_title="Announcements")


@bp.route("/notifications")
@login_required
def notifications():
    rows = Notification.query.filter_by(employee_id=current_user.id).order_by(Notification.created_at.desc()).limit(80).all()
    unread = [n.id for n in rows if not n.is_read]
    resp = render_template("portal/notifications.html", rows=rows, unread=set(unread), page_title="Notifications")
    if unread:
        Notification.query.filter(Notification.id.in_(unread)).update({"is_read": True}, synchronize_session=False)
        db.session.commit()
    return resp


@bp.route("/password", methods=["POST"])
@login_required
def password():
    if current_user.check_password(request.form.get("old", "")) and len(request.form.get("new", "")) >= 4:
        current_user.set_password(request.form["new"])
        db.session.commit()
        flash("Password changed.", "ok")
    else:
        flash("Current password is wrong or the new one is too short.", "err")
    return redirect(request.referrer or url_for("me.home"))
