"""Finance engine: ledger posting, balances, summaries, tips, P&L and balance sheet."""
from datetime import date, timedelta

from sqlalchemy import case, func

from ..extensions import db
from ..helpers import all_branches, inr
from ..models import (Account, Category, Employee, PurchaseBill, StockMovement, Transaction)
from . import whatsapp as wa

H_SERVICE = "Service Charges"
H_PRODUCT = "Product Sales (Retail)"
H_TIPS = "Tips"
H_GST_IN = "GST Collected"
H_TIP_OUT = "Tips Payout"
H_SALARY = "Salaries & Wages"
H_INCENTIVE = "Incentives & Commission"
H_VENDOR = "Vendor Payment – Stock"
H_GST_OUT = "GST Remitted"
H_DRAWINGS = "Owner Drawings"
H_CAPITAL = "Owner Capital Introduced"
H_PETTY = "Petty Cash & Misc"
H_REIMB = "Staff Reimbursements"

MODE_LABEL = {"cash": "Cash", "upi": "UPI", "card": "Credit/Debit Card", "cheque": "Cheque", "bank": "Bank transfer"}
COGS_KINDS = ("consumption", "sale", "wastage", "adjustment")
INCOME_NATURES = ("revenue", "tip_in", "gst_in", "equity_in")     # heads whose normal entry is money IN


def _signed(nature):
    """Amount signed so that the head's normal direction is positive; a reversal (void / refund) nets against it."""
    pos = "in" if nature in INCOME_NATURES else "out"
    return case((Transaction.kind == pos, Transaction.amount), else_=-Transaction.amount)


_CAT_IDS = {}


def clear_cache():
    _CAT_IDS.clear()


def cat(name):
    cid = _CAT_IDS.get(name)
    if cid:
        c = db.session.get(Category, cid)
        if c and c.name == name:
            return c
    c = Category.query.filter_by(name=name).first()
    if c:
        _CAT_IDS[name] = c.id
    return c


# ----------------------------------------------------------------------------
# scope helpers
# ----------------------------------------------------------------------------
def _full_scope(branch_ids):
    return set(branch_ids) >= {b.id for b in all_branches()}


def tx_scope(q, branch_ids):
    if _full_scope(branch_ids):
        return q
    return q.filter(Transaction.branch_id.in_(branch_ids))


def scope_accounts(branch_ids, active_only=True):
    q = Account.query
    if active_only:
        q = q.filter_by(active=True)
    accs = q.order_by(Account.branch_id, Account.type, Account.name).all()
    full = _full_scope(branch_ids)
    return [a for a in accs if (a.branch_id in branch_ids) or (a.branch_id is None and full)]


def resolve_account(branch_id, mode, account_id=None):
    """Pick the ledger account for a payment mode."""
    if account_id:
        a = db.session.get(Account, int(account_id))
        if a:
            return a
    want = {"cash": "cash", "card": "card", "upi": "upi", "cheque": "bank", "bank": "bank"}.get(mode, "cash")
    q = Account.query.filter_by(type=want, active=True)
    a = q.filter_by(branch_id=branch_id).first() or q.filter(Account.branch_id.is_(None)).first() or q.first()
    return a


def record(kind, amount, category, account, on_date, branch_id, mode=None, **kw):
    """Post a ledger row (caller commits)."""
    t = Transaction(kind=kind, amount=round(float(amount), 2), category_id=category.id if category else None,
                    account_id=account.id, date=on_date, branch_id=branch_id, mode=mode, **kw)
    db.session.add(t)
    return t


# ----------------------------------------------------------------------------
# balances
# ----------------------------------------------------------------------------
def _sum_by(col, cond, upto=None, since=None):
    q = db.session.query(col, func.coalesce(func.sum(Transaction.amount), 0)).filter(cond)
    if upto:
        q = q.filter(Transaction.date <= upto)
    if since:
        q = q.filter(Transaction.date >= since)
    return dict(q.group_by(col).all())


def balance_map(upto=None):
    ins = _sum_by(Transaction.account_id, Transaction.kind == "in", upto)
    outs = _sum_by(Transaction.account_id, Transaction.kind == "out", upto)
    tout = _sum_by(Transaction.account_id, Transaction.kind == "transfer", upto)
    tin = _sum_by(Transaction.to_account_id, Transaction.kind == "transfer", upto)
    return {a.id: round((a.opening_balance or 0) + ins.get(a.id, 0) - outs.get(a.id, 0) - tout.get(a.id, 0) + tin.get(a.id, 0), 2)
            for a in Account.query.all()}


def period_flows(d1, d2):
    ins = _sum_by(Transaction.account_id, Transaction.kind == "in", d2, d1)
    outs = _sum_by(Transaction.account_id, Transaction.kind == "out", d2, d1)
    tout = _sum_by(Transaction.account_id, Transaction.kind == "transfer", d2, d1)
    tin = _sum_by(Transaction.to_account_id, Transaction.kind == "transfer", d2, d1)
    return ins, outs, tout, tin


def account_statement(account_id, d1=None, d2=None):
    a = db.session.get(Account, account_id)
    d1 = d1 or date(2000, 1, 1)
    d2 = d2 or date.today()
    opening = balance_map(d1 - timedelta(days=1))[a.id] if d1 > date(2000, 1, 1) else a.opening_balance or 0
    q = Transaction.query.filter(Transaction.date.between(d1, d2)).filter(
        (Transaction.account_id == a.id) | (Transaction.to_account_id == a.id)).order_by(Transaction.date, Transaction.id)
    run = opening
    rows = []
    for t in q:
        if t.kind == "in":
            dr, cr = t.amount, 0
        elif t.kind == "out":
            dr, cr = 0, t.amount
        else:
            dr, cr = (t.amount, 0) if t.to_account_id == a.id else (0, t.amount)
        run += dr - cr
        rows.append(dict(t=t, dr=dr, cr=cr, bal=round(run, 2)))
    return a, opening, rows, round(run, 2)


# ----------------------------------------------------------------------------
# tips
# ----------------------------------------------------------------------------
def _tip_sum(branch_ids, nature, upto=None, since=None):
    q = (db.session.query(Transaction.employee_id, func.coalesce(func.sum(_signed(nature)), 0))
         .join(Category, Transaction.category_id == Category.id).filter(Category.nature == nature))
    q = tx_scope(q, branch_ids)
    if upto:
        q = q.filter(Transaction.date <= upto)
    if since:
        q = q.filter(Transaction.date >= since)
    return dict(q.group_by(Transaction.employee_id).all())


def tip_balance(emp_id, branch_ids=None, upto=None):
    branch_ids = branch_ids or [b.id for b in all_branches()]
    tin = _tip_sum(branch_ids, "tip_in", upto).get(emp_id, 0)
    tout = _tip_sum(branch_ids, "tip_out", upto).get(emp_id, 0)
    return round(tin - tout, 2)


def tips_table(branch_ids, d1, d2):
    earned = _tip_sum(branch_ids, "tip_in", d2, d1)
    paid = _tip_sum(branch_ids, "tip_out", d2, d1)
    before = d1 - timedelta(days=1)
    o_in, o_out = _tip_sum(branch_ids, "tip_in", before), _tip_sum(branch_ids, "tip_out", before)
    c_in, c_out = _tip_sum(branch_ids, "tip_in", d2), _tip_sum(branch_ids, "tip_out", d2)
    ids = set(earned) | set(paid) | {k for k in c_in} | {k for k in c_out}
    rows = []
    for eid in ids:
        if eid is None:
            continue
        e = db.session.get(Employee, eid)
        opening = round(o_in.get(eid, 0) - o_out.get(eid, 0), 2)
        closing = round(c_in.get(eid, 0) - c_out.get(eid, 0), 2)
        r = dict(emp=e, opening=opening, earned=round(earned.get(eid, 0), 2), paid=round(paid.get(eid, 0), 2), closing=closing)
        if r["opening"] or r["earned"] or r["paid"] or r["closing"]:
            rows.append(r)
    rows.sort(key=lambda r: (-r["earned"], r["emp"].name))
    return rows


def pay_tips(emp, amount, mode, account_id, branch_id, on_date, by_id=None, note=None, silent=False):
    """Pay (full / partial) accumulated tips to an employee. Returns (txn, balance_after)."""
    acc = resolve_account(branch_id, mode, account_id)
    c = cat(H_TIP_OUT)
    t = record("out", amount, c, acc, on_date, branch_id, mode=mode, party=emp.name, employee_id=emp.id,
               notes=note or "Tips payout", created_by_id=by_id)
    db.session.flush()
    bal = tip_balance(emp.id) - float(amount)
    if not silent:
        wa.tip_paid(emp, amount, max(bal, 0), MODE_LABEL.get(mode, mode), on_date, branch_id)
        from ..helpers import notify
        notify(emp.id, "Tips paid", f"{inr(amount)} tips paid via {MODE_LABEL.get(mode, mode)}. Balance {inr(max(bal, 0))}", "/me/earnings", "money")
    return t, bal


# ----------------------------------------------------------------------------
# summaries
# ----------------------------------------------------------------------------
def summary(branch_ids, d1, d2):
    S = {"d1": d1, "d2": d2}
    q = (db.session.query(Category.name, Category.nature, Transaction.kind, Transaction.mode,
                          func.coalesce(func.sum(Transaction.amount), 0))
         .join(Category, Transaction.category_id == Category.id)
         .filter(Transaction.date.between(d1, d2), Transaction.kind != "transfer"))
    q = tx_scope(q, branch_ids)
    heads_in, heads_out, mode_in, mode_out = {}, {}, {}, {}
    rev = exp = tips_in = tips_out = 0.0
    for name, nature, kind, mode, amt in q.group_by(Category.name, Category.nature, Transaction.kind, Transaction.mode).all():
        (mode_in if kind == "in" else mode_out)[mode] = (mode_in if kind == "in" else mode_out).get(mode, 0) + amt      # physical money flow
        inc = nature in INCOME_NATURES
        signed = amt if (kind == "in") == inc else -amt          # a void / refund nets against its original head
        target = heads_in if inc else heads_out
        target[(name, nature)] = target.get((name, nature), 0) + signed
        if nature == "revenue":
            rev += signed
        elif nature == "tip_in":
            tips_in += signed
        elif nature == "expense":
            exp += signed
        elif nature == "tip_out":
            tips_out += signed
    S["income_heads"] = sorted(((n, nat, round(a, 2)) for (n, nat), a in heads_in.items()), key=lambda r: -r[2])
    S["expense_heads"] = sorted(((n, nat, round(a, 2)) for (n, nat), a in heads_out.items()), key=lambda r: -r[2])
    S["mode_in"] = {k: round(v, 2) for k, v in mode_in.items()}
    S["mode_out"] = {k: round(v, 2) for k, v in mode_out.items()}
    S["in_total"] = round(sum(mode_in.values()), 2)
    S["out_total"] = round(sum(mode_out.values()), 2)
    S["revenue"], S["expenses"] = round(rev, 2), round(exp, 2)
    S["tips_in"], S["tips_out"] = round(tips_in, 2), round(tips_out, 2)

    # accounts
    opening = balance_map(d1 - timedelta(days=1))
    closing = balance_map(d2)
    ins, outs, tout, tin = period_flows(d1, d2)
    accs = []
    for a in scope_accounts(branch_ids, active_only=False):
        row = dict(acc=a, opening=opening[a.id], inflow=round(ins.get(a.id, 0), 2), outflow=round(outs.get(a.id, 0), 2),
                   xfer=round(tin.get(a.id, 0) - tout.get(a.id, 0), 2), closing=closing[a.id])
        if a.active or row["opening"] or row["inflow"] or row["outflow"] or row["closing"]:
            accs.append(row)
    S["accounts"] = accs
    for t in ("cash", "upi", "bank", "card"):
        S[t + "_total"] = round(sum(r["closing"] for r in accs if r["acc"].type == t), 2)
    S["cash_opening"] = round(sum(r["opening"] for r in accs if r["acc"].type == "cash"), 2)
    S["tips"] = tips_table(branch_ids, d1, d2)
    return S


def daily_series(branch_ids, d1, d2):
    """Revenue (business income) & expense per day for charts."""
    q = (db.session.query(Transaction.date, Category.nature, func.sum(case((Transaction.kind == "in", Transaction.amount), else_=-Transaction.amount)))
         .join(Category, Transaction.category_id == Category.id)
         .filter(Transaction.date.between(d1, d2), Category.nature.in_(("revenue", "expense", "tip_in"))))
    q = tx_scope(q, branch_ids)
    out = {}
    for d, nat, amt in q.group_by(Transaction.date, Category.nature).all():
        out.setdefault(d, {"revenue": 0, "expense": 0, "tip_in": 0})[nat] = -amt if nat == "expense" else amt
    return out


# ----------------------------------------------------------------------------
# P&L and balance sheet
# ----------------------------------------------------------------------------
def stock_cost(branch_ids, d1, d2):
    q = (db.session.query(func.coalesce(func.sum(-StockMovement.qty * StockMovement.unit_cost), 0))
         .filter(StockMovement.kind.in_(COGS_KINDS), StockMovement.date.between(d1, d2),
                 StockMovement.branch_id.in_(branch_ids)))
    return round(q.scalar() or 0, 2)


def pnl(branch_ids, d1, d2):
    S = summary(branch_ids, d1, d2)
    rev_rows = [(n, a) for n, nat, a in S["income_heads"] if nat == "revenue"]
    exp_rows = [(n, a) for n, nat, a in S["expense_heads"] if nat == "expense"]
    sc = stock_cost(branch_ids, d1, d2)
    tr, te = round(sum(a for _, a in rev_rows), 2), round(sum(a for _, a in exp_rows), 2)
    return dict(revenue_rows=rev_rows, expense_rows=exp_rows, total_revenue=tr, total_expense=te, stock_cost=sc,
                gross_profit=round(tr - sc, 2), net_profit=round(tr - sc - te, 2), margin=round((tr - sc - te) / tr * 100, 1) if tr else 0)


def inventory_value(branch_ids, upto):
    q = db.session.query(func.coalesce(func.sum(StockMovement.qty * StockMovement.unit_cost), 0)).filter(
        StockMovement.branch_id.in_(branch_ids), StockMovement.date <= upto)
    return round(q.scalar() or 0, 2)


def vendor_payable(branch_ids, upto):
    bills = db.session.query(func.coalesce(func.sum(PurchaseBill.total), 0)).filter(
        PurchaseBill.branch_id.in_(branch_ids), PurchaseBill.date <= upto).scalar() or 0
    q = (db.session.query(func.coalesce(func.sum(_signed("vendor_pay")), 0)).join(Category, Transaction.category_id == Category.id)
         .filter(Category.nature == "vendor_pay", Transaction.date <= upto))
    paid = tx_scope(q, branch_ids).scalar() or 0
    return round(bills - paid, 2)


def _nature_net(branch_ids, upto, nat_in, nat_out):
    """Liability = money collected (nat_in) less money paid out (nat_out); reversals net automatically."""
    q = (db.session.query(Category.nature, func.coalesce(func.sum(case((Transaction.kind == "in", Transaction.amount), else_=-Transaction.amount)), 0))
         .join(Category, Transaction.category_id == Category.id)
         .filter(Category.nature.in_((nat_in, nat_out)), Transaction.date <= upto))
    d = dict(tx_scope(q, branch_ids).group_by(Category.nature).all())      # net inflow per nature
    return round(d.get(nat_in, 0) + d.get(nat_out, 0), 2)


def position(branch_ids, as_of):
    bm = balance_map(as_of)
    accs = scope_accounts(branch_ids, active_only=False)
    by_type = {"cash": [], "bank": [], "upi": [], "card": []}
    for a in accs:
        by_type[a.type].append((a, bm[a.id]))
    cash = sum(v for _, v in by_type["cash"])
    bank = sum(v for _, v in by_type["bank"])
    upi = sum(v for _, v in by_type["upi"])
    card = sum(v for _, v in by_type["card"])
    inv = inventory_value(branch_ids, as_of)
    assets = round(cash + bank + upi + card + inv, 2)
    tips = _nature_net(branch_ids, as_of, "tip_in", "tip_out")
    gst = _nature_net(branch_ids, as_of, "gst_in", "gst_out")
    vend = vendor_payable(branch_ids, as_of)
    liab = round(tips + gst + vend, 2)
    return dict(as_of=as_of, by_type=by_type, cash=round(cash, 2), bank=round(bank, 2), upi=round(upi, 2), card=round(card, 2),
                inventory=inv, assets=assets, tips=tips, gst=gst, vendors=vend, liabilities=liab, equity=round(assets - liab, 2))


def balance_sheet(branch_ids, d1, d2):
    op = position(branch_ids, d1 - timedelta(days=1))
    cl = position(branch_ids, d2)
    pl = pnl(branch_ids, d1, d2)
    q = (db.session.query(Category.nature, func.coalesce(func.sum(case((Transaction.kind == "in", Transaction.amount), else_=-Transaction.amount)), 0))
         .join(Category, Transaction.category_id == Category.id)
         .filter(Category.nature.in_(("equity_in", "equity_out")), Transaction.date.between(d1, d2)))
    eq = dict(tx_scope(q, branch_ids).group_by(Category.nature).all())      # net inflow per nature
    cap, draw = round(eq.get("equity_in", 0), 2), round(-eq.get("equity_out", 0), 2)
    other = round(cl["equity"] - op["equity"] - pl["net_profit"] - cap + draw, 2)   # inter-branch transfers / unexplained
    return dict(open=op, close=cl, pnl=pl, capital=cap, drawings=draw, other=other, d1=d1, d2=d2)
