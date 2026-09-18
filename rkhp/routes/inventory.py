from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..helpers import (all_branches, audit, form_branch_id, get_period, guard_branch, manager_only, notify, notify_branch_managers, notify_supers, parse_date, require_perm,
                       scope_ids, super_only, to_float, to_int)
from ..logic import finance as fin
from ..logic import inventory as il
from ..logic import whatsapp as wa
from ..models import (MOVE_KINDS, Branch, Employee, Item, PurchaseBill, StockLevel, StockMovement, StockRequest, Transaction, Vendor)

bp = Blueprint("inv", __name__, url_prefix="/inventory")


@bp.route("/")
@require_perm("inventory")
def index():
    ids = scope_ids()
    cat, low_only, s = request.args.get("cat"), request.args.get("low") == "1", (request.args.get("q") or "").strip()
    q = StockLevel.query.join(Item, Item.id == StockLevel.item_id).filter(StockLevel.branch_id.in_(ids), Item.active == True)  # noqa: E712
    if cat:
        q = q.filter(Item.category == cat)
    if low_only:
        q = q.filter(StockLevel.qty <= StockLevel.reorder_level)
    if s:
        q = q.filter(Item.name.ilike(f"%{s}%"))
    rows = q.order_by(Item.category, Item.name, StockLevel.branch_id).all()
    value = fin.inventory_value(ids, date.today())
    low_n = len(il.low_items(ids))
    cats = [c for (c,) in db.session.query(Item.category).distinct().order_by(Item.category) if c]
    pend = StockRequest.query.filter(StockRequest.branch_id.in_(ids), StockRequest.status == "pending").count()
    return render_template("inventory/index.html", rows=rows, value=value, low_n=low_n, cats=cats, cat=cat, low_only=low_only, q=s, pend=pend, page_title="Inventory", multi=len(ids) > 1)


@bp.route("/items", methods=["GET", "POST"])
@require_perm("inventory")
def items():
    if request.method == "POST":
        if not current_user.is_manager:
            abort(403)
        f = request.form
        it = db.session.get(Item, to_int(f.get("id"))) if f.get("id") else Item()
        it.name, it.category, it.unit = f["name"].strip(), f.get("category"), f.get("unit") or "pcs"
        it.cost, it.sell_price = to_float(f.get("cost")), to_float(f.get("sell_price"))
        it.is_retail, it.vendor_id = f.get("is_retail") == "1", to_int(f.get("vendor_id"))
        it.sku = f.get("sku") or it.sku
        if f.get("id"):
            it.active = f.get("active", "1") == "1"
        else:
            db.session.add(it)
            db.session.flush()
            for b in Branch.query.all():
                sl = il.level(it.id, b.id)
                sl.reorder_level = to_float(f.get("reorder"))
        audit("save", "item", it.id, it.name)
        db.session.commit()
        flash("Item saved.", "ok")
        return redirect(url_for("inv.items"))
    rows = Item.query.order_by(Item.category, Item.name).all()
    return render_template("inventory/items.html", rows=rows, vendors=Vendor.query.filter_by(active=True).order_by(Vendor.name).all(), page_title="Item master")


@bp.route("/reorder", methods=["POST"])
@require_perm("inventory")
def reorder():
    sl = db.session.get(StockLevel, to_int(request.form.get("id"))) or abort(404)
    guard_branch(sl.branch_id)
    sl.reorder_level = to_float(request.form.get("level"))
    db.session.commit()
    flash(f"Reserve limit for {sl.item.name} set to {sl.reorder_level:g} {sl.item.unit}.", "ok")
    return redirect(request.referrer or url_for("inv.index"))


@bp.route("/receive", methods=["GET", "POST"])
@require_perm("inventory")
def receive():
    if request.method == "POST":
        try:
            bid = form_branch_id()
            guard_branch(bid)
            f = request.form
            v = db.session.get(Vendor, to_int(f.get("vendor_id")))
            if not v or not bid:
                raise ValueError("Choose the branch and vendor.")
            d = parse_date(f.get("date"), date.today())
            lines = [(int(i), to_float(q), to_float(r)) for i, q, r in zip(f.getlist("item"), f.getlist("qty"), f.getlist("rate")) if i and to_float(q) > 0]
            if not lines:
                raise ValueError("Add at least one item with a quantity.")
            bill = PurchaseBill(vendor_id=v.id, branch_id=bid, bill_no=f.get("bill_no"), date=d, note=f.get("note"))
            db.session.add(bill)
            db.session.flush()
            total = 0
            for iid, q, r in lines:
                it = db.session.get(Item, iid)
                rate = r or it.cost
                il.move(it, bid, "purchase", q, d, unit_cost=rate, vendor_id=v.id, bill_id=bill.id, note=f"Bill {bill.bill_no or bill.id}", by_id=current_user.id, alert=False)
                if r:
                    it.cost = r           # last purchase cost
                total += q * rate
            bill.total = round(total, 2)
            paid = min(to_float(f.get("paid")), bill.total)
            if paid > 0:
                mode = f.get("mode", "cash")
                acct = fin.resolve_account(bid, mode, to_int(f.get("account_id")))
                fin.record("out", paid, fin.cat(fin.H_VENDOR), acct, d, bid, mode=mode, party=v.name, vendor_id=v.id, reference=f.get("reference"),
                           notes=f"Payment for bill {bill.bill_no or bill.id}", created_by_id=current_user.id)
                bill.paid = paid
            for sr in StockRequest.query.filter(StockRequest.id == to_int(f.get("request_id"))):
                sr.status = "received"
            audit("receive", "purchase", bill.id, f"{v.name} {bill.total:g}", bid)
            db.session.commit()
            flash(f"Stock received: {len(lines)} item(s), bill value {bill.total:,.0f}" + (f", paid {paid:,.0f}" if paid else ", on credit") + ".", "ok")
            return redirect(url_for("inv.index"))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "err")
    ids = scope_ids()
    return render_template("inventory/receive.html", items=Item.query.filter_by(active=True).order_by(Item.name).all(), vendors=Vendor.query.filter_by(active=True).order_by(Vendor.name).all(),
                           accounts=[dict(id=a.id, name=a.name, type=a.type) for a in fin.scope_accounts(ids)], today=date.today(), req=db.session.get(StockRequest, to_int(request.args.get("req"))) if request.args.get("req") else None,
                           page_title="Receive stock")


@bp.route("/use", methods=["GET", "POST"])
@require_perm("inventory")
def use():
    if request.method == "POST":
        try:
            bid = form_branch_id()
            guard_branch(bid)
            f = request.form
            kind = f.get("kind", "consumption")
            d = parse_date(f.get("date"), date.today())
            n = 0
            for i, q in zip(f.getlist("item"), f.getlist("qty")):
                if i and to_float(q) > 0:
                    it = db.session.get(Item, int(i))
                    sl = il.level(it.id, bid)
                    if kind in ("consumption", "wastage") and to_float(q) > sl.qty + 0.001:
                        raise ValueError(f"Only {sl.qty:g} {it.unit} of {it.name} is in stock at this branch.")
                    il.move(it, bid, kind, to_float(q), d, employee_id=to_int(f.get("employee_id")), note=f.get("note") or ("Used" if kind == "consumption" else "Wastage"), by_id=current_user.id)
                    n += 1
            if not n:
                raise ValueError("Add at least one item and quantity.")
            audit("stock_out", "inventory", None, f"{kind} x{n}", bid)
            db.session.commit()
            flash(f"{n} item(s) deducted from stock.", "ok")
            return redirect(url_for("inv.index"))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "err")
    ids = scope_ids()
    emps = Employee.query.filter(Employee.active == True, Employee.branch_id.in_(ids)).order_by(Employee.name).all()  # noqa: E712
    stock = {}
    for sl in StockLevel.query.filter(StockLevel.branch_id.in_(ids)):
        stock[f"{sl.branch_id}:{sl.item_id}"] = sl.qty
    return render_template("inventory/use.html", items=Item.query.filter_by(active=True).order_by(Item.name).all(), emps=emps, stock=stock, today=date.today(), page_title="Use / deduct stock",
                           kind=request.args.get("kind", "consumption"))


@bp.route("/count", methods=["POST"])
@require_perm("inventory")
def count():
    """Physical stock count: set the system quantity to the counted quantity."""
    bid = form_branch_id()
    guard_branch(bid)
    it = db.session.get(Item, to_int(request.form.get("item"))) or abort(404)
    counted = to_float(request.form.get("counted"))
    sl = il.level(it.id, bid)
    delta = round(counted - sl.qty, 3)
    if abs(delta) > 0.0005:
        il.move(it, bid, "adjustment", delta, date.today(), note=request.form.get("note") or f"Physical count: {sl.qty:g} → {counted:g}", by_id=current_user.id)
        audit("count", "inventory", it.id, f"{it.name} {delta:+g}", bid)
        db.session.commit()
        flash(f"{it.name}: stock adjusted by {delta:+g} {it.unit} to match the physical count.", "ok")
    return redirect(request.referrer or url_for("inv.index"))


@bp.route("/transfer", methods=["POST"])
@manager_only
def transfer():
    it = db.session.get(Item, to_int(request.form.get("item"))) or abort(404)
    src, dst, q = to_int(request.form.get("from_branch")), to_int(request.form.get("to_branch")), to_float(request.form.get("qty"))
    guard_branch(src)
    sl = il.level(it.id, src)
    if not dst or src == dst or q <= 0 or q > sl.qty:
        flash("Choose different branches and a quantity available in the source branch.", "err")
    else:
        il.move(it, src, "transfer_out", q, note=f"To {db.session.get(Branch, dst).name}", by_id=current_user.id)
        il.move(it, dst, "transfer_in", q, note=f"From {db.session.get(Branch, src).name}", by_id=current_user.id, alert=False)
        db.session.commit()
        flash(f"Transferred {q:g} {it.unit} of {it.name}.", "ok")
    return redirect(request.referrer or url_for("inv.index"))


@bp.route("/movements")
@require_perm("inventory")
def moves():
    ids = scope_ids()
    key, d1, d2, label = get_period("mtd")
    q = StockMovement.query.filter(StockMovement.branch_id.in_(ids), StockMovement.date.between(d1, d2))
    it, kind = to_int(request.args.get("item")), request.args.get("kind")
    if it:
        q = q.filter(StockMovement.item_id == it)
    if kind:
        q = q.filter(StockMovement.kind == kind)
    rows = q.order_by(StockMovement.date.desc(), StockMovement.id.desc()).limit(400).all()
    return render_template("inventory/moves.html", rows=rows, key=key, d1=d1, d2=d2, label=label, items=Item.query.order_by(Item.name).all(), kinds=MOVE_KINDS, f=request.args, page_title="Stock movements")


@bp.route("/requests", methods=["GET", "POST"])
@require_perm("inventory")
def requests_():
    ids = scope_ids()
    if request.method == "POST":
        bid = form_branch_id()
        guard_branch(bid)
        it = db.session.get(Item, to_int(request.form.get("item"))) or abort(404)
        r = StockRequest(item_id=it.id, branch_id=bid, qty=to_float(request.form.get("qty")), urgency=request.form.get("urgency", "normal"), note=request.form.get("note"), requested_by_id=current_user.id)
        db.session.add(r)
        notify_supers("Stock request", f"{db.session.get(Branch, bid).name}: {r.qty:g} {it.unit} of {it.name} ({r.urgency})", "/approvals", "approval")
        db.session.commit()
        flash("Stock request sent to the Super Admin.", "ok")
        return redirect(url_for("inv.requests_"))
    rows = StockRequest.query.filter(StockRequest.branch_id.in_(ids)).order_by(StockRequest.status != "pending", StockRequest.created_at.desc()).limit(100).all()
    return render_template("inventory/requests.html", rows=rows, items=Item.query.filter_by(active=True).order_by(Item.name).all(), low=il.low_items(ids), page_title="Stock requests")


@bp.route("/requests/<int:rid>/<act>", methods=["POST"])
@super_only
def request_decide(rid, act):
    r = db.session.get(StockRequest, rid) or abort(404)
    r.status = "approved" if act == "approve" else "rejected"
    r.decided_by_id, r.decided_at, r.decision_note = current_user.id, datetime.now(), request.form.get("note")
    for m in Employee.query.filter(Employee.id == r.requested_by_id):
        wa.simple("stock_decision", m, qty=f"{r.qty:g}", unit=r.item.unit, item=r.item.name, branch=r.branch.name, status=r.status, extra=f" {r.decision_note}" if r.decision_note else "")
    notify(r.requested_by_id, f"Stock request {r.status}", f"{r.qty:g} {r.item.unit} of {r.item.name}", "/inventory/requests", "approval")
    db.session.commit()
    flash(f"Request {r.status}.", "ok")
    return redirect(request.referrer or url_for("inv.requests_"))


# ------------------------------- vendors -------------------------------
@bp.route("/vendors", methods=["GET", "POST"])
@require_perm("inventory", "finance")
def vendors():
    if request.method == "POST":
        f = request.form
        v = db.session.get(Vendor, to_int(f.get("id"))) if f.get("id") else Vendor()
        v.name, v.category, v.contact, v.mobile, v.gstin, v.address, v.terms = f["name"].strip(), f.get("category"), f.get("contact"), f.get("mobile"), f.get("gstin"), f.get("address"), f.get("terms")
        if f.get("id"):
            v.active = f.get("active", "1") == "1"
        else:
            db.session.add(v)
        db.session.commit()
        flash("Vendor saved.", "ok")
        return redirect(url_for("inv.vendors"))
    ids = scope_ids()
    rows = []
    for v in Vendor.query.order_by(Vendor.name):
        bills = PurchaseBill.query.filter(PurchaseBill.vendor_id == v.id, PurchaseBill.branch_id.in_(ids)).all()
        rows.append(dict(v=v, n=len(bills), total=sum(b.total for b in bills), due=sum(b.due for b in bills)))
    return render_template("inventory/vendors.html", rows=rows, page_title="Vendors")


@bp.route("/vendors/<int:vid>")
@require_perm("inventory", "finance")
def vendor_view(vid):
    v = db.session.get(Vendor, vid) or abort(404)
    ids = scope_ids()
    bills = PurchaseBill.query.filter(PurchaseBill.vendor_id == vid, PurchaseBill.branch_id.in_(ids)).order_by(PurchaseBill.date.desc()).all()
    pays = fin.tx_scope(Transaction.query, ids).filter(Transaction.vendor_id == vid).order_by(Transaction.date.desc()).all()
    return render_template("inventory/vendor_view.html", v=v, bills=bills, pays=pays, due=sum(b.due for b in bills), accounts=fin.scope_accounts(ids), MODE=fin.MODE_LABEL, page_title=v.name)


@bp.route("/vendors/<int:vid>/pay", methods=["POST"])
@require_perm("inventory", "finance")
def vendor_pay(vid):
    v = db.session.get(Vendor, vid) or abort(404)
    ids = scope_ids()
    amt = to_float(request.form.get("amount"))
    bills = PurchaseBill.query.filter(PurchaseBill.vendor_id == vid, PurchaseBill.branch_id.in_(ids)).order_by(PurchaseBill.date).all()
    due = sum(b.due for b in bills)
    if amt <= 0 or amt > due + 0.5:
        flash(f"Enter an amount up to the outstanding {due:,.0f}.", "err")
    else:
        left = amt
        for b in bills:                        # settle the oldest bills first, per branch
            if left <= 0:
                break
            pay = min(b.due, left)
            if pay > 0:
                mode = request.form.get("mode", "cash")
                acct = fin.resolve_account(b.branch_id, mode, to_int(request.form.get("account_id")))
                fin.record("out", pay, fin.cat(fin.H_VENDOR), acct, parse_date(request.form.get("date"), date.today()), b.branch_id, mode=mode, party=v.name, vendor_id=v.id,
                           reference=request.form.get("reference"), notes=f"Payment against bill {b.bill_no or b.id}", created_by_id=current_user.id)
                b.paid = round(b.paid + pay, 2)
                left -= pay
        audit("pay", "vendor", v.id, f"{v.name} {amt:g}")
        db.session.commit()
        flash(f"Paid {amt:,.0f} to {v.name}.", "ok")
    return redirect(url_for("inv.vendor_view", vid=vid))
