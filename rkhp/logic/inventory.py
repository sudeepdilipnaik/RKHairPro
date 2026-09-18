"""Inventory engine: every stock change goes through `move()` so levels, valuation and alerts stay consistent."""
from datetime import date

from ..extensions import db
from ..helpers import notify_branch_managers
from ..models import Item, ServiceConsumable, StockLevel, StockMovement
from . import whatsapp as wa

OUT_KINDS = ("consumption", "sale", "wastage", "transfer_out", "return")


def level(item_id, branch_id, create=True):
    sl = StockLevel.query.filter_by(item_id=item_id, branch_id=branch_id).first()
    if not sl and create:
        sl = StockLevel(item_id=item_id, branch_id=branch_id, qty=0, reorder_level=0)
        db.session.add(sl)
        db.session.flush()
    return sl


def move(item, branch_id, kind, qty, on_date=None, unit_cost=None, employee_id=None, invoice_id=None, vendor_id=None,
         bill_id=None, note=None, by_id=None, alert=True):
    """Post a stock movement. `qty` is positive; direction comes from `kind` (adjustment may be signed)."""
    qty = float(qty)
    if kind in OUT_KINDS:
        qty = -abs(qty)
    elif kind != "adjustment":
        qty = abs(qty)
    sl = level(item.id, branch_id)
    before = sl.qty
    sl.qty = round(sl.qty + qty, 3)
    if unit_cost is None:
        unit_cost = item.cost or 0
    m = StockMovement(item_id=item.id, branch_id=branch_id, kind=kind, qty=qty, unit_cost=unit_cost, date=on_date or date.today(),
                      employee_id=employee_id, invoice_id=invoice_id, vendor_id=vendor_id, bill_id=bill_id, note=note, created_by_id=by_id)
    db.session.add(m)
    rl = sl.reorder_level or 0
    if alert and qty < 0 and before > rl and sl.qty <= rl:      # just crossed the reserve limit
        raise_low_stock(item, sl)
    return m


def raise_low_stock(item, sl):
    from ..models import Branch
    br = db.session.get(Branch, sl.branch_id)
    ids = notify_branch_managers(sl.branch_id, f"Low stock: {item.name}",
                                 f"{sl.qty:g} {item.unit} left at {br.name} (reorder level {sl.reorder_level:g})", "/inventory?low=1", "stock")
    from ..models import Employee
    for e in Employee.query.filter(Employee.id.in_(ids)):
        wa.simple("low_stock", e, branch=br.name, item=item.name, qty=f"{sl.qty:g}", unit=item.unit, level=f"{sl.reorder_level:g}")


def deduct_for_service(service_id, branch_id, invoice_id, on_date, employee_id=None, by_id=None):
    """Auto-consume the recipe of a service when it's billed."""
    for sc in ServiceConsumable.query.filter_by(service_id=service_id).all():
        move(sc.item, branch_id, "consumption", sc.qty, on_date, employee_id=employee_id, invoice_id=invoice_id,
             note="Auto: service billed", by_id=by_id)


def low_items(branch_ids):
    return (StockLevel.query.join(Item, Item.id == StockLevel.item_id)
            .filter(StockLevel.branch_id.in_(branch_ids), Item.active == True, StockLevel.qty <= StockLevel.reorder_level)  # noqa: E712
            .order_by(StockLevel.qty).all())
