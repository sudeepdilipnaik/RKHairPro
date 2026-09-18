from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..helpers import (all_branches, audit, form_branch_id, fy_label, get_period, guard_branch, manager_only, parse_date, require_perm, scope_ids,
                       super_only, to_float, to_int)
from ..logic import finance as fin
from ..models import ACCOUNT_TYPES, NATURES, Account, Category, DayClose, Employee, Transaction

bp = Blueprint("fin", __name__, url_prefix="/finance")


def _accounts_json(branch_ids):
    return [dict(id=a.id, name=a.name, type=a.type, branch=a.branch_id) for a in fin.scope_accounts(branch_ids)]


@bp.route("/")
@require_perm("finance")
def index():
    ids = scope_ids()
    key, d1, d2, label = get_period("today")
    S = fin.summary(ids, d1, d2)
    P = fin.pnl(ids, d1, d2)
    day_closes = DayClose.query.filter(DayClose.branch_id.in_(ids), DayClose.date == d2).all() if d1 == d2 else []
    return render_template("finance/index.html", S=S, P=P, key=key, d1=d1, d2=d2, label=label, closes=day_closes, MODE=fin.MODE_LABEL, page_title="Finance",
                           cash_by_branch=[(b, fin.summary([b.id], d1, d2)) for b in all_branches() if b.id in ids] if len(ids) > 1 else [])


@bp.route("/ledger")
@require_perm("finance")
def ledger():
    ids = scope_ids()
    key, d1, d2, label = get_period("mtd")
    q = fin.tx_scope(Transaction.query, ids).filter(Transaction.date.between(d1, d2))
    kind, head, acc, s = request.args.get("kind"), to_int(request.args.get("head")), to_int(request.args.get("acc")), (request.args.get("q") or "").strip()
    if kind:
        q = q.filter(Transaction.kind == kind)
    if head:
        q = q.filter(Transaction.category_id == head)
    if acc:
        q = q.filter((Transaction.account_id == acc) | (Transaction.to_account_id == acc))
    if s:
        q = q.filter((Transaction.party.ilike(f"%{s}%")) | (Transaction.notes.ilike(f"%{s}%")) | (Transaction.batch.ilike(f"%{s}%")))
    rows = q.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(400).all()
    tin = sum(t.amount for t in rows if t.kind == "in")
    tout = sum(t.amount for t in rows if t.kind == "out")
    return render_template("finance/ledger.html", rows=rows, key=key, d1=d1, d2=d2, label=label, tin=tin, tout=tout, heads=Category.query.order_by(Category.kind, Category.name).all(),
                           accounts=fin.scope_accounts(ids, False), f=request.args, page_title="Ledger / day book", MODE=fin.MODE_LABEL)


@bp.route("/entry", methods=["GET", "POST"])
@require_perm("finance")
def entry():
    kind = request.values.get("kind", "out")
    ids = scope_ids()
    if request.method == "POST":
        try:
            bid = form_branch_id()
            if not bid:
                raise ValueError("Choose a branch.")
            guard_branch(bid)
            amount = to_float(request.form.get("amount"))
            if amount <= 0:
                raise ValueError("Enter an amount greater than zero.")
            head = db.session.get(Category, to_int(request.form.get("category_id")))
            mode = request.form.get("mode")
            acct = fin.resolve_account(bid, mode, to_int(request.form.get("account_id")))
            emp_id = to_int(request.form.get("employee_id"))
            if head.nature in ("tip_in",) and not emp_id:
                raise ValueError("Select which employee the tip belongs to.")
            d = parse_date(request.form.get("date"), date.today())
            t = fin.record("in" if head.kind == "income" else "out", amount, head, acct, d, bid, mode=mode, party=request.form.get("party"),
                           reference=request.form.get("reference"), notes=request.form.get("notes"), employee_id=emp_id, petty=request.form.get("petty") == "1",
                           created_by_id=current_user.id)
            audit("create", "transaction", None, f"{head.name} {amount:g}", bid)
            db.session.commit()
            flash(f"{head.name}: {amount:,.0f} recorded in <b>{acct.name}</b>.", "ok")
            return redirect(url_for("fin.ledger", p="today"))
        except (ValueError, AttributeError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, ValueError) else "Please fill all required fields.", "err")
    heads = Category.query.filter_by(kind="income" if kind == "in" else "expense", active=True).order_by(Category.group, Category.name).all()
    heads = [h for h in heads if h.nature not in ("tip_out", "vendor_pay") or kind == "out"]
    emps = Employee.query.filter(Employee.active == True, Employee.role != "super_admin", Employee.branch_id.in_(ids)).order_by(Employee.name).all()  # noqa: E712
    return render_template("finance/entry.html", kind=kind, heads=heads, emps=emps, accounts_json=_accounts_json(ids), petty=request.args.get("petty") == "1",
                           page_title="Add income" if kind == "in" else "Add expense", today=date.today(), f=request.form)


@bp.route("/transfer", methods=["POST"])
@require_perm("finance")
def transfer():
    a, b = db.session.get(Account, to_int(request.form.get("from_id"))), db.session.get(Account, to_int(request.form.get("to_id")))
    amt = to_float(request.form.get("amount"))
    if not (a and b) or a.id == b.id or amt <= 0:
        flash("Choose two different accounts and a valid amount.", "err")
    else:
        bal = fin.balance_map()[a.id]
        bid = a.branch_id or b.branch_id or (scope_ids()[0])
        db.session.add(Transaction(kind="transfer", amount=amt, date=parse_date(request.form.get("date"), date.today()), account_id=a.id, to_account_id=b.id, branch_id=bid,
                                   mode=a.type if a.type in ("cash", "upi") else "bank", notes=request.form.get("notes") or f"Transfer {a.name} → {b.name}", created_by_id=current_user.id,
                                   batch=f"TRF-{datetime.now():%y%m%d%H%M}"))
        audit("transfer", "account", a.id, f"{a.name}->{b.name} {amt:g}")
        db.session.commit()
        flash(f"Moved {amt:,.0f} from <b>{a.name}</b> to <b>{b.name}</b>." + (" (Note: source balance is now negative.)" if bal < amt else ""), "ok")
    return redirect(request.referrer or url_for("fin.accounts"))


@bp.route("/accounts")
@require_perm("finance")
def accounts():
    ids = scope_ids()
    bm = fin.balance_map()
    accs = fin.scope_accounts(ids, active_only=False)
    return render_template("finance/accounts.html", accs=accs, bm=bm, types=ACCOUNT_TYPES, page_title="Accounts", total=sum(bm[a.id] for a in accs if a.active))


@bp.route("/accounts/save", methods=["POST"])
@require_perm("finance")
def account_save():
    f = request.form
    a = db.session.get(Account, to_int(f.get("id"))) if f.get("id") else Account()
    if a.id:
        guard_branch(a.branch_id)
    a.name, a.type = f["name"].strip(), f["type"]
    if not a.id:
        a.opening_balance = to_float(f.get("opening_balance"))
        a.branch_id = current_user.branch_id or to_int(f.get("branch_id")) or None
        db.session.add(a)
    elif current_user.is_super:
        a.opening_balance = to_float(f.get("opening_balance"), a.opening_balance)
    a.upi_id, a.bank_name, a.notes = f.get("upi_id"), f.get("bank_name"), f.get("notes")
    if a.id:
        a.active = f.get("active", "1") == "1"
    audit("save", "account", a.id, a.name)
    db.session.commit()
    flash("Account saved.", "ok")
    return redirect(url_for("fin.accounts"))


@bp.route("/accounts/<int:aid>")
@require_perm("finance")
def account_view(aid):
    a = db.session.get(Account, aid) or abort(404)
    guard_branch(a.branch_id)
    key, d1, d2, label = get_period("mtd")
    acc, opening, rows, closing = fin.account_statement(aid, d1, d2)
    return render_template("finance/account_view.html", a=acc, opening=opening, rows=rows, closing=closing, key=key, d1=d1, d2=d2, label=label, page_title=acc.name, MODE=fin.MODE_LABEL)


# ------------------------------- tips -------------------------------
@bp.route("/tips")
@require_perm("finance")
def tips():
    ids = scope_ids()
    key, d1, d2, label = get_period("today")
    T = fin.tips_table(ids, d1, d2)
    accounts = fin.scope_accounts(ids)
    return render_template("finance/tips.html", T=T, key=key, d1=d1, d2=d2, label=label, accounts=accounts, page_title="Tips", MODE=fin.MODE_LABEL,
                           total_due=sum(r["closing"] for r in T), total_earned=sum(r["earned"] for r in T), total_paid=sum(r["paid"] for r in T))


@bp.route("/tips/pay", methods=["POST"])
@require_perm("finance")
def tips_pay():
    emp = db.session.get(Employee, to_int(request.form.get("employee_id")))
    amt = to_float(request.form.get("amount"))
    if not emp:
        abort(404)
    bid = emp.branch_id or emp.home_branch_id
    guard_branch(bid)
    bal = fin.tip_balance(emp.id)
    if amt <= 0 or amt > bal + 0.5:
        flash(f"Amount must be between 1 and the outstanding tip balance ({bal:,.0f}).", "err")
    else:
        fin.pay_tips(emp, amt, request.form.get("mode", "cash"), to_int(request.form.get("account_id")), bid, date.today(), current_user.id)
        audit("tips", "employee", emp.id, f"paid {amt:g}", bid)
        db.session.commit()
        flash(f"Paid {amt:,.0f} tips to <b>{emp.name}</b>. Balance now {max(bal - amt, 0):,.0f}. <span class='badge'>WhatsApp sent to employee (demo)</span>", "ok")
    return redirect(request.referrer or url_for("fin.tips"))


@bp.route("/tips/pay-all", methods=["POST"])
@require_perm("finance")
def tips_pay_all():
    """End-of-day: pay the full balance to every employee on 'daily' payout (salary-mode employees keep accumulating)."""
    ids = scope_ids()
    n = total = 0
    for r in fin.tips_table(ids, date.today(), date.today()):
        e = r["emp"]
        if e.tip_mode == "daily" and r["closing"] > 0 and (e.branch_id or e.home_branch_id) in ids:
            fin.pay_tips(e, r["closing"], request.form.get("mode", "cash"), None, e.branch_id or e.home_branch_id, date.today(), current_user.id)
            n += 1; total += r["closing"]
    db.session.commit()
    flash(f"Paid {total:,.0f} in tips to {n} employee(s) on daily payout. Employees on 'with salary' keep accumulating.", "ok" if n else "err")
    return redirect(request.referrer or url_for("fin.tips"))


# ------------------------------- day close -------------------------------
@bp.route("/day-close", methods=["GET", "POST"])
@require_perm("finance")
def day_close():
    ids = scope_ids()
    d = parse_date(request.values.get("date"), date.today())
    bid = to_int(request.values.get("b")) or ids[0]
    if bid not in ids:
        abort(403)
    S = fin.summary([bid], d, d)
    expected = S["cash_total"]
    dc = DayClose.query.filter_by(branch_id=bid, date=d).first()
    if request.method == "POST":
        counted = to_float(request.form.get("counted"))
        dc = dc or DayClose(branch_id=bid, date=d)
        dc.expected_cash, dc.counted_cash, dc.difference = expected, counted, round(counted - expected, 2)
        dc.note, dc.closed_by_id, dc.closed_at = request.form.get("note"), current_user.id, datetime.now()
        db.session.add(dc)
        audit("close", "day", None, f"{d} diff {dc.difference:g}", bid)
        db.session.commit()
        flash("Day closed. " + ("Cash tallies perfectly ✔" if abs(dc.difference) < 1 else f"Cash difference of {dc.difference:+,.0f} recorded."), "ok")
        return redirect(url_for("fin.day_close", date=d.isoformat(), b=bid))
    hist = DayClose.query.filter(DayClose.branch_id.in_(ids)).order_by(DayClose.date.desc()).limit(15).all()
    from ..models import Branch
    return render_template("finance/day_close.html", S=S, d=d, bid=bid, br=db.session.get(Branch, bid), dc=dc, expected=expected, hist=hist, all_b=Branch.query.filter(Branch.id.in_(ids)).all(),
                           page_title="Day close", pending_tips=[r for r in S["tips"] if r["closing"] > 0])


# ------------------------------- balance sheet -------------------------------
@bp.route("/balance-sheet")
@require_perm("finance", "reports")
def balance_sheet():
    ids = scope_ids()
    key, d1, d2, label = get_period("mtd")
    B = fin.balance_sheet(ids, d1, d2)
    return render_template("finance/balance_sheet.html", B=B, key=key, d1=d1, d2=d2, label=label, page_title="Balance sheet", fy=fy_label(), full=len(ids) > 1)


# ------------------------------- heads -------------------------------
@bp.route("/heads", methods=["GET", "POST"])
@require_perm("finance")
def heads():
    if request.method == "POST":
        if not current_user.is_super:
            abort(403)
        f = request.form
        c = db.session.get(Category, to_int(f.get("id"))) if f.get("id") else Category(system=False)
        if c.system and f.get("id"):
            c.group = f.get("group")
            c.active = f.get("active", "1") == "1"
        else:
            c.name, c.kind, c.group = f["name"].strip(), f["kind"], f.get("group")
            c.nature = f.get("nature") or ("revenue" if c.kind == "income" else "expense")
            c.active = f.get("active", "1") == "1"
            db.session.add(c)
        audit("save", "head", None, c.name)
        db.session.commit()
        flash("Head saved.", "ok")
        return redirect(url_for("fin.heads"))
    rows = Category.query.order_by(Category.kind, Category.group, Category.name).all()
    usage = dict(db.session.query(Transaction.category_id, func.count(Transaction.id)).group_by(Transaction.category_id).all())
    return render_template("finance/heads.html", rows=rows, usage=usage, natures=NATURES, page_title="Income & expense heads")
