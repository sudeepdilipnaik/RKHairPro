"""Billing engine: invoice creation, payment posting to the ledger, void."""
from datetime import date, datetime

from ..extensions import db
from ..helpers import audit, fy_start, gst_on, setting_num, token
from ..models import (Appointment, Branch, Client, Coupon, Feedback, Invoice, InvoiceItem, InvoiceTip, Item, Service,
                      Transaction)
from . import finance as fin
from . import inventory as inv_logic
from . import whatsapp as wa


class BillError(Exception):
    pass


def next_number(branch, on_date):
    s = fy_start(on_date)
    code = f"{str(s.year)[2:]}{str(s.year + 1)[2:]}"
    prefix = f"{branch.code}-{code}-"
    n = Invoice.query.filter(Invoice.number.like(prefix + "%")).count() + 1
    while Invoice.query.filter_by(number=f"{prefix}{n:05d}").first():
        n += 1
    return f"{prefix}{n:05d}"


def check_coupon(code, subtotal, on_date=None):
    """Return (coupon, discount_amount) or raise BillError."""
    if not code:
        return None, 0.0
    on_date = on_date or date.today()
    c = Coupon.query.filter(db.func.upper(Coupon.code) == code.strip().upper()).first()
    if not c or not c.active:
        raise BillError(f"Coupon '{code}' is not valid.")
    if c.valid_from and on_date < c.valid_from or c.valid_to and on_date > c.valid_to:
        raise BillError(f"Coupon {c.code} is not valid on this date.")
    if c.usage_limit and c.used_count >= c.usage_limit:
        raise BillError(f"Coupon {c.code} has reached its usage limit.")
    if subtotal < (c.min_bill or 0):
        raise BillError(f"Coupon {c.code} needs a minimum bill of ₹{c.min_bill:g}.")
    disc = subtotal * c.value / 100.0 if c.kind == "percent" else c.value
    if c.max_discount:
        disc = min(disc, c.max_discount)
    return c, round(min(disc, subtotal), 2)


def _components(inv):
    comps = []
    svc = round(sum(i.net_amount for i in inv.items if i.kind == "service"), 2)
    prod = round(sum(i.net_amount for i in inv.items if i.kind == "product"), 2)
    if svc:
        comps.append((fin.cat(fin.H_SERVICE), None, svc))
    if prod:
        comps.append((fin.cat(fin.H_PRODUCT), None, prod))
    if inv.gst_amount:
        comps.append((fin.cat(fin.H_GST_IN), None, round(inv.gst_amount, 2)))
    for t in inv.tips:
        if t.amount:
            comps.append((fin.cat(fin.H_TIPS), t.employee_id, round(t.amount, 2)))
    return comps


def post_payments(inv, payments, on_date, by_id=None):
    """Split each payment across the bill's heads (service / product / GST / tips) and post to the ledger."""
    comps = _components(inv)
    total = sum(a for _, _, a in comps)
    if not total:
        return
    party = inv.client.name if inv.client else "Walk-in guest"
    for p in payments:
        amt = round(float(p["amount"]), 2)
        if amt <= 0:
            continue
        acct = fin.resolve_account(inv.branch_id, p["mode"], p.get("account_id"))
        share = amt / total
        alloc = [round(a * share, 2) for _, _, a in comps]
        alloc[alloc.index(max(alloc))] += round(amt - sum(alloc), 2)          # rounding remainder
        for (c, emp, _), a in zip(comps, alloc):
            if a > 0:
                fin.record("in", a, c, acct, on_date, inv.branch_id, mode=p["mode"], party=party, employee_id=emp,
                           invoice_id=inv.id, batch=inv.number, reference=p.get("reference"), created_by_id=by_id,
                           notes=f"Invoice {inv.number}")


def create_invoice(branch_id, client, items, tips, payments, on_date=None, discount_amt=0.0, discount_pct=0.0,
                   coupon_code=None, appointment=None, notes=None, by_id=None, send_whatsapp=True):
    on_date = on_date or date.today()
    branch = db.session.get(Branch, branch_id)
    if not items:
        raise BillError("Add at least one service or product to the bill.")
    inv = Invoice(branch_id=branch_id, client_id=client.id if client else None, appointment_id=appointment.id if appointment else None,
                  date=on_date, notes=notes, created_by_id=by_id, number=next_number(branch, on_date))
    db.session.add(inv)
    lines = []
    for it in items:
        qty = float(it.get("qty") or 1)
        price = float(it.get("price") or 0)
        if it["kind"] == "service":
            s = db.session.get(Service, int(it["ref_id"]))
            desc, sid, iid = s.name, s.id, None
        else:
            p = db.session.get(Item, int(it["ref_id"]))
            desc, sid, iid = p.name, None, p.id
        ii = InvoiceItem(kind=it["kind"], service_id=sid, item_id=iid, description=desc, qty=qty, unit_price=price,
                         amount=round(qty * price, 2), employee_id=it.get("employee_id") or None)
        inv.items.append(ii)
        lines.append(ii)
    subtotal = round(sum(i.amount for i in lines), 2)
    coupon, cdisc = check_coupon(coupon_code, subtotal, on_date)
    manual = max(float(discount_amt or 0), round(subtotal * float(discount_pct or 0) / 100.0, 2))
    discount = round(min(subtotal, cdisc + manual), 2)
    for ii in lines:                                                # proportional discount share
        share = (ii.amount / subtotal * discount) if subtotal else 0
        ii.net_amount = round(ii.amount - share, 2)
    net = round(subtotal - discount, 2)
    gst = round(net * setting_num("gst_rate", 18) / 100.0, 2) if gst_on() else 0.0
    tip_total = 0.0
    for t in tips:
        amt = round(float(t["amount"]), 2)
        if amt > 0:
            inv.tips.append(InvoiceTip(employee_id=int(t["employee_id"]), amount=amt))
            tip_total += amt
    inv.subtotal, inv.discount, inv.gst_amount = subtotal, discount, gst
    inv.coupon_code = coupon.code if coupon else None
    inv.tip_total = round(tip_total, 2)
    inv.grand_total = round(net + gst + tip_total, 2)
    paid = round(sum(float(p["amount"]) for p in payments), 2)
    if paid > inv.grand_total + 0.5:
        raise BillError(f"Payments ({paid:g}) exceed the bill ({inv.grand_total:g}). Add the extra as a tip.")
    inv.paid_amount = min(paid, inv.grand_total)
    inv.status = "paid" if inv.due <= 0.5 else "partial"
    db.session.flush()
    post_payments(inv, payments, on_date, by_id)
    # stock
    for ii in inv.items:
        if ii.kind == "service" and ii.service_id:
            inv_logic.deduct_for_service(ii.service_id, branch_id, inv.id, on_date, ii.employee_id, by_id)
        elif ii.kind == "product" and ii.item_id:
            inv_logic.move(db.session.get(Item, ii.item_id), branch_id, "sale", ii.qty, on_date, invoice_id=inv.id,
                           employee_id=ii.employee_id, note=f"Sold on {inv.number}", by_id=by_id)
    # client side effects
    fb = None
    if client:
        earn = int(net // setting_num("loyalty_earn_per", 100)) if setting_num("loyalty_earn_per", 100) else 0
        client.loyalty_points = (client.loyalty_points or 0) + earn
        primary = next((i.employee_id for i in inv.items if i.employee_id), None)
        fb = Feedback(token=token(10), invoice_id=inv.id, client_id=client.id, branch_id=branch_id, employee_id=primary)
        db.session.add(fb)
    if coupon:
        coupon.used_count = (coupon.used_count or 0) + 1
    if appointment:
        appointment.status = "completed"
        appointment.invoice_id = inv.id
    audit("create", "invoice", inv.id, f"{inv.number} {inv.grand_total:g}", branch_id)
    db.session.flush()
    if send_whatsapp and client and paid > 0:
        wa.payment_thanks(inv, [(fin.MODE_LABEL.get(p["mode"], p["mode"]), float(p["amount"])) for p in payments if float(p["amount"]) > 0],
                          fb.token if fb else None)
    return inv


def add_payment(inv, payments, on_date=None, by_id=None):
    on_date = on_date or date.today()
    paid = round(sum(float(p["amount"]) for p in payments), 2)
    if paid <= 0:
        raise BillError("Enter an amount to collect.")
    if paid > inv.due + 0.5:
        raise BillError(f"Amount exceeds the balance due ({inv.due:g}).")
    post_payments(inv, payments, on_date, by_id)
    inv.paid_amount = round(inv.paid_amount + paid, 2)
    inv.status = "paid" if inv.due <= 0.5 else "partial"
    if inv.client:
        wa.payment_thanks(inv, [(fin.MODE_LABEL.get(p["mode"], p["mode"]), float(p["amount"])) for p in payments if float(p["amount"]) > 0],
                          paid_now=paid)
    audit("payment", "invoice", inv.id, f"{inv.number} +{paid:g}", inv.branch_id)


def void_invoice(inv, by_id=None, reason=""):
    if inv.status == "void":
        return
    for t in Transaction.query.filter_by(invoice_id=inv.id).all():
        if t.notes and t.notes.startswith("VOID"):
            continue
        fin.record("out" if t.kind == "in" else "in", t.amount, t.category, t.account, date.today(), t.branch_id, mode=t.mode,
                   party=t.party, employee_id=t.employee_id, invoice_id=inv.id, batch=inv.number,
                   notes=f"VOID reversal of {inv.number}", created_by_id=by_id)
    if inv.client:
        inv.client.loyalty_points = max(0, (inv.client.loyalty_points or 0) - int(inv.net_business // setting_num("loyalty_earn_per", 100)))
    inv.status = "void"
    inv.notes = ((inv.notes or "") + f" | VOID: {reason}").strip(" |")
    audit("void", "invoice", inv.id, f"{inv.number}: {reason}", inv.branch_id)
