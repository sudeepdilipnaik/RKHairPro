from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..helpers import clean_mobile, parse_date, require_perm, scope_ids, to_int
from ..logic import whatsapp as wa
from ..models import Appointment, Branch, Client, Invoice

bp = Blueprint("clients", __name__, url_prefix="/clients")


def stats_query(ids):
    return dict((cid, (n, spent, last)) for cid, n, spent, last in
                db.session.query(Invoice.client_id, func.count(Invoice.id), func.sum(Invoice.subtotal - Invoice.discount), func.max(Invoice.date))
                .filter(Invoice.branch_id.in_(ids), Invoice.status != "void", Invoice.client_id.isnot(None)).group_by(Invoice.client_id))


@bp.route("/")
@require_perm("appointments", "marketing")
def index():
    ids = scope_ids()
    q = (request.args.get("q") or "").strip()
    seg = request.args.get("seg", "all")
    st = stats_query(ids)
    query = Client.query
    if q:
        query = query.filter((Client.name.ilike(f"%{q}%")) | (Client.mobile.like(f"%{q}%")))
    if not current_user.is_super:
        # branch users see clients who have visited or booked their branch, or have that home branch
        seen = {cid for cid in st} | {a.client_id for a in Appointment.query.filter(Appointment.branch_id.in_(ids))}
        query = query.filter((Client.id.in_(seen)) | (Client.branch_id.in_(ids)))
    today = date.today()
    rows = []
    for c in query.order_by(Client.name).limit(600):
        n, spent, last = st.get(c.id, (0, 0, None))
        tag = "New" if n <= 1 and (today - c.created_at.date()).days < 45 else ("VIP" if spent and spent >= 25000 else ("Lapsed" if last and (today - last).days > 60 else ("Regular" if n >= 4 else "")))
        bday = c.dob and c.dob.month == today.month
        if seg == "vip" and tag != "VIP" or seg == "lapsed" and tag != "Lapsed" or seg == "new" and tag != "New" or seg == "birthday" and not bday:
            continue
        rows.append(dict(c=c, n=n, spent=spent or 0, last=last, tag=tag, bday=bday))
    return render_template("clients/index.html", rows=rows, q=q, seg=seg, page_title="Clients")


@bp.route("/save", methods=["POST"])
@require_perm("appointments", "marketing")
def save():
    cid = to_int(request.form.get("id"))
    mobile = clean_mobile(request.form.get("mobile"))
    if len(mobile) != 10:
        flash("Enter a valid 10-digit mobile number.", "err")
        return redirect(request.referrer or url_for("clients.index"))
    c = db.session.get(Client, cid) if cid else None
    if not c:
        if Client.query.filter_by(mobile=mobile).first():
            flash("A client with this mobile number already exists.", "err")
            return redirect(url_for("clients.index"))
        c = Client(mobile=mobile)
        db.session.add(c)
    c.name = request.form["name"].strip()
    c.mobile = mobile
    c.email = request.form.get("email")
    c.gender = request.form.get("gender")
    c.dob = parse_date(request.form.get("dob"))
    c.anniversary = parse_date(request.form.get("anniversary"))
    c.source = request.form.get("source") or c.source
    c.notes = request.form.get("notes")
    c.branch_id = c.branch_id or (current_user.branch_id or to_int(request.form.get("branch_id")))
    db.session.commit()
    flash("Client saved.", "ok")
    return redirect(url_for("clients.view", cid=c.id))


@bp.route("/<int:cid>")
@require_perm("appointments", "marketing")
def view(cid):
    c = db.session.get(Client, cid) or abort(404)
    ids = scope_ids()
    invs = Invoice.query.filter(Invoice.client_id == cid, Invoice.branch_id.in_(ids)).order_by(Invoice.date.desc()).limit(30).all()
    appts = Appointment.query.filter(Appointment.client_id == cid, Appointment.branch_id.in_(ids)).order_by(Appointment.start_dt.desc()).limit(20).all()
    spent = sum(i.net_business for i in invs if i.status != "void")
    return render_template("clients/view.html", c=c, invs=invs, appts=appts, spent=spent, page_title=c.name)


@bp.route("/<int:cid>/message", methods=["POST"])
@require_perm("appointments", "marketing")
def message(cid):
    c = db.session.get(Client, cid) or abort(404)
    body = (request.form.get("body") or "").strip()
    if body:
        wa.queue("campaign", c.mobile, c.name, body, current_user.branch_id)
        db.session.commit()
        flash(f"WhatsApp message queued to {c.name} <span class='badge'>demo – see WhatsApp Outbox</span>", "wa")
    return redirect(url_for("clients.view", cid=cid))
