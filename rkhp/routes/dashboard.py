from datetime import date, datetime, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..helpers import fy_label, fy_start, scope_ids
from ..logic import appointments as al
from ..logic import finance as fin
from ..logic import inventory as inv_logic
from ..models import (Announcement, Branch, Employee, Invoice, InvoiceItem, LeaveRequest, Reimbursement, RoleRequest, StockRequest, WhatsAppMessage)

bp = Blueprint("dash", __name__)


@bp.route("/dashboard")
@login_required
def index():
    ids = scope_ids()
    today = date.today()
    mtd, ytd = today.replace(day=1), fy_start(today)
    P = current_user.has_perm
    ctx = dict(page_title="Dashboard", today=today, mtd_start=mtd, ytd_start=ytd, fy=fy_label())
    if P("finance") or P("reports"):
        s_day, s_mtd, s_ytd = fin.summary(ids, today, today), fin.summary(ids, mtd, today), fin.summary(ids, ytd, today)
        pl_mtd, pl_ytd = fin.pnl(ids, mtd, today), fin.pnl(ids, ytd, today)
        ctx.update(s_day=s_day, s_mtd=s_mtd, s_ytd=s_ytd, pl_mtd=pl_mtd, pl_ytd=pl_ytd)
        ser = fin.daily_series(ids, today - timedelta(days=13), today)
        days = [today - timedelta(days=i) for i in range(13, -1, -1)]
        ctx["trend"] = dict(labels=[d.strftime("%d %b") for d in days],
                            series=[dict(name="Sales", values=[ser.get(d, {}).get("revenue", 0) for d in days]),
                                    dict(name="Expenses", values=[ser.get(d, {}).get("expense", 0) for d in days])])
        # branch cards
        cards = []
        for b in Branch.query.filter(Branch.id.in_(ids)):
            pm = fin.pnl([b.id], mtd, today)
            pt = fin.pnl([b.id], today, today)
            n_ap = len(al.day_appointments([b.id], today, include_cancelled=False))
            cards.append(dict(b=b, today=pt["total_revenue"], mtd=pm["total_revenue"], profit=pm["net_profit"], appts=n_ap,
                              pct=round(pm["total_revenue"] / b.monthly_target * 100) if b.monthly_target else 0))
        ctx["cards"] = cards
        top = (db.session.query(InvoiceItem.employee_id, func.sum(InvoiceItem.net_amount)).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
               .filter(InvoiceItem.kind == "service", Invoice.branch_id.in_(ids), Invoice.date.between(mtd, today), Invoice.status != "void", InvoiceItem.employee_id.isnot(None))
               .group_by(InvoiceItem.employee_id).order_by(func.sum(InvoiceItem.net_amount).desc()).limit(5).all())
        ctx["top"] = [(db.session.get(Employee, e), v) for e, v in top]
        ctx["dues"] = Invoice.query.filter(Invoice.branch_id.in_(ids), Invoice.status == "partial").order_by(Invoice.date).limit(5).all()
    if P("appointments"):
        appts = al.day_appointments(ids, today)
        groups = {}
        for a in appts:
            groups.setdefault(a.employee, []).append(a)
        order = sorted(groups.items(), key=lambda kv: (kv[0] is None or kv[0].role != "super_admin", kv[0].name if kv[0] else "~"))
        ctx["appt_groups"] = order
        ctx["appt_count"] = sum(1 for a in appts if a.is_active)
        ctx["upcoming"] = [a for a in appts if a.start_dt >= datetime.now() and a.is_active][:6]
    if P("inventory"):
        ctx["low"] = inv_logic.low_items(ids)[:6]
    if current_user.is_manager:
        ctx["pend"] = dict(
            leaves=LeaveRequest.query.join(Employee, Employee.id == LeaveRequest.employee_id).filter(LeaveRequest.status == "pending", Employee.branch_id.in_(ids)).count(),
            reimb=Reimbursement.query.filter(Reimbursement.status == "pending", Reimbursement.branch_id.in_(ids)).count(),
            stock=StockRequest.query.filter(StockRequest.status == "pending", StockRequest.branch_id.in_(ids)).count(),
            roles=RoleRequest.query.filter_by(status="pending").count() if current_user.is_super else 0)
        ctx["wa"] = WhatsAppMessage.query.order_by(WhatsAppMessage.id.desc()).limit(4).all()
    ctx["announcements"] = Announcement.query.order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).limit(3).all()
    last_inv = db.session.query(func.max(Invoice.date)).scalar()
    ctx["stale_demo"] = bool(current_user.is_super and last_inv and (today - last_inv).days > 1)
    return render_template("dashboard.html", **ctx)
