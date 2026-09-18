"""Cross-cutting screens: approvals hub and WhatsApp outbox."""
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import manager_only, scope_ids, to_int
from ..logic import finance as fin
from ..logic import whatsapp as wa
from ..models import Employee, LeaveRequest, Reimbursement, RoleRequest, StockRequest, WhatsAppMessage

bp = Blueprint("misc", __name__)


@bp.route("/approvals")
@manager_only
def approvals():
    ids = scope_ids()
    leaves = LeaveRequest.query.join(Employee, Employee.id == LeaveRequest.employee_id).filter(LeaveRequest.status == "pending", Employee.branch_id.in_(ids)).order_by(LeaveRequest.from_date).all()
    reimbs = Reimbursement.query.filter(Reimbursement.status == "pending", Reimbursement.branch_id.in_(ids)).order_by(Reimbursement.date).all()
    approved_daily = Reimbursement.query.filter(Reimbursement.status == "approved", Reimbursement.settle_mode == "daily", Reimbursement.branch_id.in_(ids)).all()
    stock = StockRequest.query.filter(StockRequest.status == "pending", StockRequest.branch_id.in_(ids)).order_by(StockRequest.created_at).all()
    roles = RoleRequest.query.filter_by(status="pending").order_by(RoleRequest.created_at).all() if current_user.is_super else []
    return render_template("approvals.html", leaves=leaves, reimbs=reimbs, approved_daily=approved_daily, stock=stock, roles=roles, accounts=fin.scope_accounts(ids),
                           total=len(leaves) + len(reimbs) + len(stock) + len(roles), page_title="Approvals")


@bp.route("/whatsapp")
@manager_only
def outbox():
    ids = scope_ids()
    kind, q = request.args.get("kind"), (request.args.get("q") or "").strip()
    query = WhatsAppMessage.query.filter((WhatsAppMessage.branch_id.in_(ids)) | (WhatsAppMessage.branch_id.is_(None)))
    if kind:
        query = query.filter(WhatsAppMessage.kind == kind)
    if q:
        query = query.filter((WhatsAppMessage.to_name.ilike(f"%{q}%")) | (WhatsAppMessage.to_mobile.like(f"%{q}%")) | (WhatsAppMessage.body.ilike(f"%{q}%")))
    rows = query.order_by(WhatsAppMessage.created_at.desc(), WhatsAppMessage.id.desc()).limit(80).all()
    counts = dict(db.session.query(WhatsAppMessage.kind, db.func.count(WhatsAppMessage.id)).group_by(WhatsAppMessage.kind).all())
    return render_template("outbox.html", rows=rows, kinds=wa.TEMPLATES, counts=counts, kind=kind, q=q, wa_link=wa.wa_link, page_title="WhatsApp outbox")


@bp.route("/whatsapp/reminders", methods=["POST"])
@manager_only
def run_reminders():
    n = wa.send_due_reminders()
    flash(f"Reminder job ran: {n} appointment reminder(s) queued (sent 1 hour before each appointment).", "ok")
    return redirect(url_for("misc.outbox"))


@bp.route("/whatsapp/send", methods=["POST"])
@manager_only
def send_custom():
    mobile, body = request.form.get("mobile", ""), (request.form.get("body") or "").strip()
    if len("".join(c for c in mobile if c.isdigit())) >= 10 and body:
        wa.queue("campaign", mobile, request.form.get("name") or "Contact", body, current_user.branch_id)
        db.session.commit()
        flash("Message queued <span class='badge'>demo</span>", "wa")
    else:
        flash("Enter a valid mobile number and a message.", "err")
    return redirect(url_for("misc.outbox"))
