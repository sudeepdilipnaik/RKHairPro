from datetime import date, datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..helpers import (all_branches, clean_mobile, form_branch_id, gst_on, guard_branch, manager_only, parse_date, require_perm, scope_ids, setting_num,
                       to_float, to_int)
from ..logic import appointments as al
from ..logic import billing as bl
from ..logic import finance as fin
from ..models import Appointment, Branch, Client, Coupon, Employee, Invoice, Item, Service, ServiceCategory, StockLevel, Transaction

bp = Blueprint("billing", __name__, url_prefix="/billing")
SERVICE_ROLES = ("senior_stylist", "stylist", "assistant", "branch_admin", "super_admin")


def _pos_context(branch_id, d):
    emps = [e for e in Employee.query.filter(Employee.active == True, Employee.role.in_(SERVICE_ROLES)).order_by(Employee.name)  # noqa: E712
            if e.location_on(d) == branch_id or e.branch_id == branch_id]
    accounts = fin.scope_accounts([branch_id])
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = [dict(id=s.id, name=s.name, price=s.price, cat=s.category.name if s.category else "", dur=s.duration) for s in Service.query.filter_by(active=True).order_by(Service.name)]
    prods = [dict(id=i.id, name=i.name, price=i.sell_price, stock=(StockLevel.query.filter_by(item_id=i.id, branch_id=branch_id).first() or StockLevel(qty=0)).qty)
             for i in Item.query.filter_by(is_retail=True, active=True).order_by(Item.name)]
    return emps, accounts, svcs, prods


@bp.route("/new", methods=["GET", "POST"])
@require_perm("billing")
def new():
    appt = db.session.get(Appointment, to_int(request.values.get("appt"))) if request.values.get("appt") else None
    if appt:
        guard_branch(appt.branch_id)
    bid = form_branch_id() or (appt.branch_id if appt else None) or scope_ids()[0]
    if request.method == "POST":
        bid = form_branch_id() or bid
        guard_branch(bid)
        try:
            f = request.form
            client = None
            mobile = clean_mobile(f.get("mobile"))
            if mobile:
                if len(mobile) != 10:
                    raise bl.BillError("Client mobile number must be 10 digits.")
                client = al.find_or_create_client((f.get("name") or "Guest").strip(), mobile, "Walk-in", bid)
            items = []
            for k, r, e, q, p in zip(f.getlist("item_kind"), f.getlist("item_ref"), f.getlist("item_emp"), f.getlist("item_qty"), f.getlist("item_price")):
                if r:
                    items.append(dict(kind=k, ref_id=int(r), employee_id=to_int(e), qty=to_float(q, 1), price=to_float(p)))
            tips = [dict(employee_id=int(e), amount=to_float(a)) for e, a in zip(f.getlist("tip_emp"), f.getlist("tip_amt")) if e and to_float(a) > 0]
            pays = [dict(mode=m, account_id=to_int(ac), amount=to_float(a), reference=None) for m, ac, a in zip(f.getlist("pay_mode"), f.getlist("pay_acct"), f.getlist("pay_amt")) if to_float(a) > 0]
            appt_id = to_int(f.get("appointment_id"))
            inv = bl.create_invoice(bid, client, items, tips, pays, on_date=parse_date(f.get("date"), date.today()), discount_amt=to_float(f.get("discount_amt")),
                                    discount_pct=to_float(f.get("discount_pct")), coupon_code=(f.get("coupon") or "").strip() or None,
                                    appointment=db.session.get(Appointment, appt_id) if appt_id else None, notes=f.get("notes"), by_id=current_user.id)
            db.session.commit()
            msg = f"Invoice <b>{inv.number}</b> saved – {inv.grand_total:,.0f} collected." if inv.status == "paid" else f"Invoice <b>{inv.number}</b> saved with balance due."
            flash(msg, "ok")
            if client and inv.paid_amount > 0:
                flash(f"WhatsApp thank-you sent to {client.name} <span class='badge'>demo – see Outbox</span>", "wa")
            return redirect(url_for("billing.view", iid=inv.id))
        except (bl.BillError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "err")
    d = date.today()
    emps, accounts, svcs, prods = _pos_context(bid, d)
    prefill = None
    if appt:
        prefill = dict(mobile=appt.client.mobile, name=appt.client.name, appt_id=appt.id, emp=appt.employee_id,
                       lines=[dict(id=l.service_id, price=l.price) for l in appt.lines])
    coupons = Coupon.query.filter_by(active=True).all()
    return render_template("billing/new.html", bid=bid, branch=db.session.get(Branch, bid), emps_j=[[e.id, e.name] for e in emps], acc_j=[[a.id, a.name, a.type] for a in accounts], svcs=svcs, prods=prods, prefill=prefill,
                           coupons=coupons, gst=gst_on(), gst_rate=setting_num("gst_rate", 18), today=d, page_title="New bill")


@bp.route("/coupon")
@require_perm("billing")
def coupon():
    try:
        c, disc = bl.check_coupon(request.args.get("code"), to_float(request.args.get("subtotal")))
        return jsonify(ok=True, discount=disc, label=c.description if c else "")
    except bl.BillError as e:
        return jsonify(ok=False, message=str(e))


@bp.route("/")
@require_perm("billing")
def list_():
    ids = scope_ids()
    d1 = parse_date(request.args.get("from"), date.today().replace(day=1))
    d2 = parse_date(request.args.get("to"), date.today())
    q = Invoice.query.filter(Invoice.branch_id.in_(ids), Invoice.date.between(d1, d2))
    st, s = request.args.get("status"), (request.args.get("q") or "").strip()
    if st:
        q = q.filter(Invoice.status == st)
    if s:
        q = q.outerjoin(Client).filter((Invoice.number.ilike(f"%{s}%")) | (Client.name.ilike(f"%{s}%")) | (Client.mobile.like(f"%{s}%")))
    rows = q.order_by(Invoice.date.desc(), Invoice.id.desc()).limit(400).all()
    tot = sum(r.grand_total for r in rows if r.status != "void")
    return render_template("billing/list.html", rows=rows, d1=d1, d2=d2, f=request.args, total=tot, page_title="Invoices")


@bp.route("/<int:iid>")
@require_perm("billing")
def view(iid):
    inv = db.session.get(Invoice, iid) or abort(404)
    guard_branch(inv.branch_id)
    txns = Transaction.query.filter_by(invoice_id=iid).order_by(Transaction.id).all()
    pays = {}
    for t in txns:
        if t.notes and t.notes.startswith("VOID"):
            continue
        k = (t.mode, t.account.name)
        pays[k] = pays.get(k, 0) + t.amount
    accounts = fin.scope_accounts([inv.branch_id])
    return render_template("billing/view.html", inv=inv, pays=pays, accounts=accounts, page_title=f"Invoice {inv.number}", modes=fin.MODE_LABEL)


@bp.route("/<int:iid>/pay", methods=["POST"])
@require_perm("billing")
def pay(iid):
    inv = db.session.get(Invoice, iid) or abort(404)
    guard_branch(inv.branch_id)
    try:
        f = request.form
        pays = [dict(mode=f["mode"], account_id=to_int(f.get("account_id")), amount=to_float(f.get("amount")))]
        bl.add_payment(inv, pays, by_id=current_user.id)
        db.session.commit()
        flash("Payment recorded. <span class='badge'>WhatsApp receipt sent (demo)</span>", "ok")
    except bl.BillError as e:
        db.session.rollback()
        flash(str(e), "err")
    return redirect(url_for("billing.view", iid=iid))


@bp.route("/<int:iid>/void", methods=["POST"])
@manager_only
def void(iid):
    inv = db.session.get(Invoice, iid) or abort(404)
    guard_branch(inv.branch_id)
    bl.void_invoice(inv, current_user.id, request.form.get("reason") or "Voided by manager")
    db.session.commit()
    flash("Invoice voided and its ledger entries reversed.", "ok")
    return redirect(url_for("billing.view", iid=iid))
