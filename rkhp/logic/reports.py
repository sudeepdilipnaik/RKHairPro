"""Report registry.  Each report is a function(R) -> dict(columns, rows, totals, kpis, chart, note)."""
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import distinct, func

from ..extensions import db
from ..helpers import daterange, gst_on, hhmm_to_min, month_bounds, setting_num
from ..models import (Appointment, Attendance, Branch, Campaign, Category, Client, Coupon, Employee, Feedback, Invoice,
                      InvoiceItem, InvoiceTip, Item, Lead, LeaveRequest, Payroll, PurchaseBill, Reimbursement, Service,
                      ServiceCategory, StockLevel, StockMovement, Transaction, Vendor)
from . import appointments as appt_logic
from . import finance as fin
from . import payroll as pr

DEPTS = [("finance", "Finance"), ("sales", "Sales & Clients"), ("appointments", "Appointments"), ("hr", "HR & Payroll"),
         ("inventory", "Inventory"), ("marketing", "Marketing"), ("mis", "Owner MIS")]
REPORTS = OrderedDict()


def report(key, dept, title, desc, period=True, month=False):
    def deco(fn):
        REPORTS[key] = dict(key=key, dept=dept, title=title, desc=desc, fn=fn, period=period, month=month)
        return fn
    return deco


def C(k, t, ty="text"):
    return dict(k=k, t=t, ty=ty)


def bname(bid):
    b = db.session.get(Branch, bid) if bid else None
    return b.name if b else "Company"


def _inv(R):
    return Invoice.query.filter(Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void")


def _pct(a, b):
    return round(a / b * 100, 1) if b else 0


def make_ctx(branch_ids, d1, d2, args=None):
    args = args or {}
    ym = args.get("month") or date.today().strftime("%Y-%m")
    return SimpleNamespace(branch_ids=branch_ids, d1=d1, d2=d2, today=date.today(), args=args, month=ym, multi=len(branch_ids) > 1)


# =============================================================================
# FINANCE
# =============================================================================
@report("daybook", "finance", "Day book", "Every rupee in and out, in time order, with head, mode and account.")
def r_daybook(R):
    q = fin.tx_scope(Transaction.query, R.branch_ids).filter(Transaction.date.between(R.d1, R.d2)).order_by(Transaction.date, Transaction.id)
    rows, ti, to = [], 0, 0
    for t in q:
        i = t.amount if t.kind == "in" else 0
        o = t.amount if t.kind == "out" else 0
        ti += i; to += o
        head = t.category.name if t.category else f"Transfer → {t.to_account.name if t.to_account else ''}"
        rows.append([t.date, t.created_at.strftime("%I:%M %p").lstrip("0"), bname(t.branch_id), head, fin.MODE_LABEL.get(t.mode, "—"),
                     t.account.name, t.party or "", t.notes or "", i, o])
    return dict(columns=[C("d", "Date", "date"), C("t", "Time"), C("b", "Branch"), C("h", "Head"), C("m", "Mode"), C("a", "Account"),
                         C("p", "Party"), C("n", "Notes"), C("i", "Money in", "money"), C("o", "Money out", "money")],
                rows=rows, totals=["Total", "", "", "", "", "", "", "", ti, to],
                kpis=[("Money in", ti, "money"), ("Money out", to, "money"), ("Net", ti - to, "money")])


@report("income_heads", "finance", "Income head-wise", "Where the money came from: services, tips, products and other heads, split by payment mode.")
def r_income_heads(R):
    S = fin.summary(R.branch_ids, R.d1, R.d2)
    q = (db.session.query(Category.name, Transaction.mode, func.sum(Transaction.amount)).join(Category, Transaction.category_id == Category.id)
         .filter(Transaction.kind == "in", Transaction.date.between(R.d1, R.d2)))
    data = defaultdict(lambda: defaultdict(float))
    for n, m, a in fin.tx_scope(q, R.branch_ids).group_by(Category.name, Transaction.mode).all():
        data[n][m] += a
    rows = [[n, d.get("cash", 0), d.get("upi", 0), d.get("card", 0), d.get("bank", 0) + d.get("cheque", 0), sum(d.values())] for n, d in data.items()]
    rows.sort(key=lambda r: -r[5])
    tot = [sum(r[i] for r in rows) for i in range(1, 6)]
    return dict(columns=[C("h", "Income head"), C("c", "Cash", "money"), C("u", "UPI", "money"), C("k", "Card", "money"), C("b", "Bank", "money"), C("t", "Total", "money")],
                rows=rows, totals=["Total"] + tot, chart=dict(labels=[r[0] for r in rows[:8]], series=[dict(name="Income", values=[r[5] for r in rows[:8]])]),
                kpis=[("Total received", S["in_total"], "money"), ("Business income", S["revenue"], "money"), ("Tips collected", S["tips_in"], "money")])


@report("expense_heads", "finance", "Expense head-wise", "Where the money went: every expense head split by payment mode, including petty cash.")
def r_expense_heads(R):
    q = (db.session.query(Category.name, Category.nature, Transaction.mode, func.sum(Transaction.amount)).join(Category, Transaction.category_id == Category.id)
         .filter(Transaction.kind == "out", Transaction.date.between(R.d1, R.d2)))
    data = defaultdict(lambda: defaultdict(float))
    nat = {}
    for n, na, m, a in fin.tx_scope(q, R.branch_ids).group_by(Category.name, Category.nature, Transaction.mode).all():
        data[n][m] += a; nat[n] = na
    rows = [[n, d.get("cash", 0), d.get("upi", 0), d.get("bank", 0) + d.get("cheque", 0), sum(d.values())] for n, d in data.items()]
    rows.sort(key=lambda r: -r[4])
    tot = [sum(r[i] for r in rows) for i in range(1, 5)]
    return dict(columns=[C("h", "Expense head"), C("c", "Cash", "money"), C("u", "UPI", "money"), C("b", "Cheque / Bank", "money"), C("t", "Total", "money")],
                rows=rows, totals=["Total"] + tot, chart=dict(labels=[r[0] for r in rows[:8]], series=[dict(name="Expense", values=[r[4] for r in rows[:8]])]),
                note="Includes pass-through payouts such as tips payout and vendor payments; see 'Profit & Loss' for true business expenses.")


@report("pnl", "finance", "Profit & loss", "Business income less cost of stock used and expenses. Tips and GST are pass-through and excluded.")
def r_pnl(R):
    P = fin.pnl(R.branch_ids, R.d1, R.d2)
    rows = [["INCOME", "", ""]]
    rows += [[f"   {n}", a, ""] for n, a in P["revenue_rows"]]
    rows += [["Total income", "", P["total_revenue"]], ["Less: cost of stock used (consumables & retail)", "", -P["stock_cost"]],
             ["Gross profit", "", P["gross_profit"]], ["EXPENSES", "", ""]]
    rows += [[f"   {n}", a, ""] for n, a in P["expense_rows"]]
    rows += [["Total expenses", "", -P["total_expense"]], ["NET PROFIT", "", P["net_profit"]]]
    return dict(columns=[C("l", "Particulars"), C("a", "Amount", "money"), C("t", "Total", "money")], rows=rows,
                kpis=[("Income", P["total_revenue"], "money"), ("Stock cost", P["stock_cost"], "money"), ("Expenses", P["total_expense"], "money"),
                      ("Net profit", P["net_profit"], "money"), ("Margin", P["margin"], "pct")], bold=("INCOME", "EXPENSES", "Total income", "Gross profit", "Total expenses", "NET PROFIT"))


@report("money_position", "finance", "Cash, bank, UPI & card position", "Opening, inflow, outflow and closing balance of every account.")
def r_position(R):
    S = fin.summary(R.branch_ids, R.d1, R.d2)
    rows = [[r["acc"].name, r["acc"].type_label, bname(r["acc"].branch_id), r["opening"], r["inflow"], r["outflow"], r["xfer"], r["closing"]] for r in S["accounts"]]
    return dict(columns=[C("a", "Account"), C("t", "Type"), C("b", "Branch"), C("o", "Opening", "money"), C("i", "Money in", "money"), C("u", "Money out", "money"),
                         C("x", "Transfers (net)", "money"), C("c", "Closing", "money")],
                rows=rows, totals=["Total", "", "", sum(r[3] for r in rows), sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows), sum(r[7] for r in rows)],
                kpis=[("Cash in counter", S["cash_total"], "money"), ("Bank", S["bank_total"], "money"), ("UPI accounts", S["upi_total"], "money"), ("Card settlement", S["card_total"], "money")])


@report("payment_modes", "finance", "Payment mode split", "Daily collection by cash, UPI and card - useful for reconciliation.")
def r_modes(R):
    q = (db.session.query(Transaction.date, Transaction.mode, func.sum(Transaction.amount)).join(Category, Transaction.category_id == Category.id)
         .filter(Transaction.kind == "in", Transaction.date.between(R.d1, R.d2)))
    data = defaultdict(lambda: defaultdict(float))
    for d, m, a in fin.tx_scope(q, R.branch_ids).group_by(Transaction.date, Transaction.mode).all():
        data[d][m] += a
    rows = [[d, v.get("cash", 0), v.get("upi", 0), v.get("card", 0), v.get("bank", 0) + v.get("cheque", 0), sum(v.values())] for d, v in sorted(data.items())]
    tot = [sum(r[i] for r in rows) for i in range(1, 6)]
    return dict(columns=[C("d", "Date", "date"), C("c", "Cash", "money"), C("u", "UPI", "money"), C("k", "Card", "money"), C("b", "Bank", "money"), C("t", "Total", "money")],
                rows=rows, totals=["Total"] + tot, chart=dict(labels=[r[0].strftime("%d %b") for r in rows[-31:]],
                                                              series=[dict(name="Cash", values=[r[1] for r in rows[-31:]]), dict(name="UPI", values=[r[2] for r in rows[-31:]]),
                                                                      dict(name="Card", values=[r[3] for r in rows[-31:]])], stacked=True),
                kpis=[("Cash", tot[0] if tot else 0, "money"), ("UPI", tot[1] if tot else 0, "money"), ("Card", tot[2] if tot else 0, "money")])


@report("tips", "finance", "Tips report – per employee", "Tips earned, paid out and still accumulated for every employee.")
def r_tips(R):
    T = fin.tips_table(R.branch_ids, R.d1, R.d2)
    rows = [[t["emp"].name, t["emp"].role_label, "Daily" if t["emp"].tip_mode == "daily" else "With salary", t["opening"], t["earned"], t["paid"], t["closing"]] for t in T]
    return dict(columns=[C("e", "Employee"), C("r", "Role"), C("m", "Payout mode"), C("o", "Opening balance", "money"), C("i", "Tips earned", "money"),
                         C("p", "Tips paid", "money"), C("c", "Balance due", "money")],
                rows=rows, totals=["Total", "", "", sum(r[3] for r in rows), sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows)],
                chart=dict(labels=[r[0] for r in rows[:10]], series=[dict(name="Earned", values=[r[4] for r in rows[:10]]), dict(name="Paid", values=[r[5] for r in rows[:10]])]))


@report("branch_compare", "finance", "Branch comparison", "Income, expenses, profit, bills and average ticket side by side for every branch.")
def r_branch_compare(R):
    rows = []
    for b in Branch.query.filter(Branch.id.in_(R.branch_ids)):
        P = fin.pnl([b.id], R.d1, R.d2)
        S = fin.summary([b.id], R.d1, R.d2)
        n = Invoice.query.filter(Invoice.branch_id == b.id, Invoice.date.between(R.d1, R.d2), Invoice.status != "void").count()
        ap = Appointment.query.filter(Appointment.branch_id == b.id, Appointment.start_dt >= datetime.combine(R.d1, datetime.min.time()),
                                      Appointment.start_dt < datetime.combine(R.d2 + timedelta(days=1), datetime.min.time()), Appointment.status != "cancelled").count()
        rows.append([b.name, P["total_revenue"], P["stock_cost"], P["total_expense"], P["net_profit"], P["margin"], n, round(P["total_revenue"] / n) if n else 0, S["tips_in"], ap])
    tot = ["Total"] + [sum(r[i] for r in rows) for i in (1, 2, 3, 4)] + [_pct(sum(r[4] for r in rows), sum(r[1] for r in rows)), sum(r[6] for r in rows), 0, sum(r[8] for r in rows), sum(r[9] for r in rows)]
    tot[7] = round(tot[1] / tot[6]) if tot[6] else 0
    return dict(columns=[C("b", "Branch"), C("r", "Income", "money"), C("s", "Stock cost", "money"), C("e", "Expenses", "money"), C("p", "Net profit", "money"), C("m", "Margin", "pct"),
                         C("n", "Bills", "int"), C("a", "Avg ticket", "money"), C("t", "Tips", "money"), C("ap", "Appointments", "int")],
                rows=rows, totals=tot, chart=dict(labels=[r[0] for r in rows], series=[dict(name="Income", values=[r[1] for r in rows]), dict(name="Net profit", values=[r[4] for r in rows])]))


@report("daily_trend", "finance", "Daily income vs expense trend", "Day-by-day business income, expenses and tips - spot slow days quickly.")
def r_trend(R):
    ser = fin.daily_series(R.branch_ids, R.d1, R.d2)
    rows = []
    for d in daterange(R.d1, R.d2):
        v = ser.get(d, {})
        rows.append([d, v.get("revenue", 0), v.get("tip_in", 0), v.get("expense", 0), v.get("revenue", 0) - v.get("expense", 0)])
    last = rows[-31:]
    return dict(columns=[C("d", "Date", "date"), C("r", "Business income", "money"), C("t", "Tips", "money"), C("e", "Expenses", "money"), C("n", "Net", "money")], rows=rows,
                totals=["Total"] + [sum(r[i] for r in rows) for i in range(1, 5)],
                chart=dict(labels=[r[0].strftime("%d %b") for r in last], series=[dict(name="Income", values=[r[1] for r in last]), dict(name="Expenses", values=[r[3] for r in last])]))


@report("vendor_dues", "finance", "Vendor payables", "Bills raised, paid and outstanding for every vendor.", period=False)
def r_vendor_dues(R):
    rows = []
    for v in Vendor.query.filter_by(active=True).order_by(Vendor.name):
        bills = PurchaseBill.query.filter(PurchaseBill.vendor_id == v.id, PurchaseBill.branch_id.in_(R.branch_ids)).all()
        tot = sum(b.total for b in bills); paid = sum(b.paid for b in bills)
        if tot:
            rows.append([v.name, v.category or "", len(bills), tot, paid, tot - paid])
    return dict(columns=[C("v", "Vendor"), C("c", "Category"), C("n", "Bills", "int"), C("t", "Billed", "money"), C("p", "Paid", "money"), C("d", "Outstanding", "money")],
                rows=rows, totals=["Total", "", sum(r[2] for r in rows), sum(r[3] for r in rows), sum(r[4] for r in rows), sum(r[5] for r in rows)])


@report("petty_cash", "finance", "Petty cash & minor expenses", "All small expenses so nothing goes unaccounted.")
def r_petty(R):
    q = fin.tx_scope(Transaction.query, R.branch_ids).filter(Transaction.petty == True, Transaction.date.between(R.d1, R.d2)).order_by(Transaction.date)  # noqa: E712
    rows = [[t.date, bname(t.branch_id), t.category.name if t.category else "", t.notes or "", t.party or "", t.created_by.name if t.created_by else "", t.amount] for t in q]
    return dict(columns=[C("d", "Date", "date"), C("b", "Branch"), C("h", "Head"), C("n", "Purpose"), C("p", "Paid to"), C("u", "Entered by"), C("a", "Amount", "money")],
                rows=rows, totals=["Total", "", "", "", "", "", sum(r[6] for r in rows)])


@report("gst_summary", "finance", "GST summary", "GST collected on invoices and remitted (available when GST is switched on in Settings).")
def r_gst(R):
    note = "" if gst_on() else "GST is currently switched OFF in Settings, so no GST is being charged."
    q = _inv(R).filter(Invoice.gst_amount > 0)
    rows = [[i.date, i.number, i.client.name if i.client else "Guest", i.net_business, i.gst_amount / 2, i.gst_amount / 2, i.gst_amount] for i in q.order_by(Invoice.date)]
    return dict(columns=[C("d", "Date", "date"), C("i", "Invoice"), C("c", "Client"), C("t", "Taxable value", "money"), C("cg", "CGST", "money"), C("sg", "SGST", "money"), C("g", "Total GST", "money")],
                rows=rows, totals=["Total", "", "", sum(r[3] for r in rows), sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows)], note=note)


# =============================================================================
# SALES & CLIENTS
# =============================================================================
@report("sales_service", "sales", "Sales by service", "Which services earn the most and how often they are sold.")
def r_sales_service(R):
    q = (db.session.query(InvoiceItem.description, ServiceCategory.name, func.sum(InvoiceItem.qty), func.sum(InvoiceItem.net_amount))
         .join(Invoice, Invoice.id == InvoiceItem.invoice_id).outerjoin(Service, Service.id == InvoiceItem.service_id).outerjoin(ServiceCategory, ServiceCategory.id == Service.category_id)
         .filter(InvoiceItem.kind == "service", Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void")
         .group_by(InvoiceItem.description, ServiceCategory.name).order_by(func.sum(InvoiceItem.net_amount).desc()))
    rows = [[n, c or "—", int(q_), a, a / q_ if q_ else 0] for n, c, q_, a in q.all()]
    tot = sum(r[3] for r in rows)
    rows = [r + [_pct(r[3], tot)] for r in rows]
    return dict(columns=[C("s", "Service"), C("c", "Category"), C("q", "Times sold", "int"), C("a", "Revenue", "money"), C("v", "Avg price", "money"), C("p", "% of sales", "pct")],
                rows=rows, totals=["Total", "", sum(r[2] for r in rows), tot, "", 100 if tot else 0],
                chart=dict(labels=[r[0] for r in rows[:10]], series=[dict(name="Revenue", values=[r[3] for r in rows[:10]])]))


@report("sales_category", "sales", "Sales by category", "Hair, colour, grooming, skin... which category drives the business.")
def r_sales_category(R):
    q = (db.session.query(func.coalesce(ServiceCategory.name, "Retail products"), func.sum(InvoiceItem.qty), func.sum(InvoiceItem.net_amount))
         .join(Invoice, Invoice.id == InvoiceItem.invoice_id).outerjoin(Service, Service.id == InvoiceItem.service_id).outerjoin(ServiceCategory, ServiceCategory.id == Service.category_id)
         .filter(Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void").group_by(ServiceCategory.name).order_by(func.sum(InvoiceItem.net_amount).desc()))
    data = q.all(); tot = sum(a for _, _, a in data)
    rows = [[n, int(c), a, _pct(a, tot)] for n, c, a in data]
    return dict(columns=[C("c", "Category"), C("q", "Items sold", "int"), C("a", "Revenue", "money"), C("p", "% of sales", "pct")], rows=rows,
                totals=["Total", sum(r[1] for r in rows), tot, 100 if tot else 0], chart=dict(labels=[r[0] for r in rows], series=[dict(name="Revenue", values=[r[2] for r in rows])]))


@report("employee_performance", "sales", "Employee performance", "Revenue, bills, average ticket, tips, incentive earned and client rating for every employee.")
def r_emp_perf(R):
    rev = {e: (a, n) for e, a, n in db.session.query(InvoiceItem.employee_id, func.sum(InvoiceItem.net_amount), func.count(distinct(InvoiceItem.invoice_id)))
           .join(Invoice, Invoice.id == InvoiceItem.invoice_id).filter(InvoiceItem.kind == "service", Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2),
                                                                        Invoice.status != "void", InvoiceItem.employee_id.isnot(None)).group_by(InvoiceItem.employee_id)}
    tips = dict(db.session.query(InvoiceTip.employee_id, func.sum(InvoiceTip.amount)).join(Invoice, Invoice.id == InvoiceTip.invoice_id)
                .filter(Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void").group_by(InvoiceTip.employee_id))
    rating = dict(db.session.query(Feedback.employee_id, func.avg(Feedback.rating)).filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(R.branch_ids),
                                                                                           func.date(Feedback.submitted_at).between(R.d1, R.d2)).group_by(Feedback.employee_id))
    rows = []
    for e in Employee.query.filter(Employee.active == True, Employee.id.in_(list(rev) + list(tips))).order_by(Employee.name):  # noqa: E712
        a, n = rev.get(e.id, (0, 0))
        rows.append([e.name, e.role_label, bname(e.branch_id or e.home_branch_id), n, a, round(a / n) if n else 0, tips.get(e.id, 0), round(a * (e.commission_pct or 0) / 100, 2),
                     round(rating[e.id], 1) if rating.get(e.id) else "—"])
    rows.sort(key=lambda r: -r[4])
    return dict(columns=[C("e", "Employee"), C("r", "Role"), C("b", "Branch"), C("n", "Bills", "int"), C("a", "Service revenue", "money"), C("t", "Avg ticket", "money"),
                         C("ti", "Tips", "money"), C("i", "Incentive earned", "money"), C("rt", "Rating ★", "text")], rows=rows,
                totals=["Total", "", "", sum(r[3] for r in rows), sum(r[4] for r in rows), "", sum(r[6] for r in rows), sum(r[7] for r in rows), ""],
                chart=dict(labels=[r[0] for r in rows[:10]], series=[dict(name="Revenue", values=[r[4] for r in rows[:10]])]))


@report("peak_hours", "sales", "Peak hours & busy days", "When the salon is busiest - plan staffing and promotions around it.")
def r_peak(R):
    hours, days = defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0, 0.0])
    for i in _inv(R):
        h = i.created_at.hour
        hours[h][0] += 1; hours[h][1] += i.net_business
        wd = i.date.strftime("%A")
        days[wd][0] += 1; days[wd][1] += i.net_business
    rows = [[f"{h % 12 or 12}:00 {'AM' if h < 12 else 'PM'}", hours[h][0], hours[h][1]] for h in sorted(hours)]
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rows += [["", "", ""]] + [[d, days[d][0], days[d][1]] for d in order if d in days]
    return dict(columns=[C("h", "Hour / Weekday"), C("n", "Bills", "int"), C("a", "Revenue", "money")], rows=rows,
                chart=dict(labels=[r[0] for r in rows if r[0] and r[0] in order], series=[dict(name="Bills", values=[r[1] for r in rows if r[0] in order])]))


@report("top_clients", "sales", "Top clients", "Highest-spending clients in the period - your VIP list.")
def r_top_clients(R):
    q = (db.session.query(Client.name, Client.mobile, func.count(Invoice.id), func.sum(Invoice.subtotal - Invoice.discount), func.max(Invoice.date))
         .join(Invoice, Invoice.client_id == Client.id).filter(Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void")
         .group_by(Client.id).order_by(func.sum(Invoice.subtotal - Invoice.discount).desc()).limit(50))
    rows = [[n, m, c, a, a / c, d] for n, m, c, a, d in q.all()]
    return dict(columns=[C("n", "Client"), C("m", "Mobile"), C("c", "Visits", "int"), C("a", "Spend", "money"), C("v", "Avg bill", "money"), C("d", "Last visit", "date")], rows=rows)


@report("new_repeat", "sales", "New vs repeat clients", "How many clients are first-timers vs returning - the health of retention.")
def r_new_repeat(R):
    rows = []
    for b in Branch.query.filter(Branch.id.in_(R.branch_ids)):
        cids = {i.client_id for i in Invoice.query.filter(Invoice.branch_id == b.id, Invoice.date.between(R.d1, R.d2), Invoice.status != "void", Invoice.client_id.isnot(None))}
        new = sum(1 for c in cids if not Invoice.query.filter(Invoice.client_id == c, Invoice.date < R.d1, Invoice.status != "void").first())
        rows.append([b.name, len(cids), new, len(cids) - new, _pct(len(cids) - new, len(cids))])
    return dict(columns=[C("b", "Branch"), C("t", "Unique clients", "int"), C("n", "New clients", "int"), C("r", "Repeat clients", "int"), C("p", "Repeat %", "pct")], rows=rows,
                totals=["Total", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows), ""])


@report("lapsed_clients", "sales", "Lapsed clients (win-back list)", "Clients who have not visited in 60+ days - call or send an offer.", period=False)
def r_lapsed(R):
    cutoff = date.today() - timedelta(days=60)
    q = (db.session.query(Client.name, Client.mobile, func.count(Invoice.id), func.sum(Invoice.subtotal - Invoice.discount), func.max(Invoice.date))
         .join(Invoice, Invoice.client_id == Client.id).filter(Invoice.branch_id.in_(R.branch_ids), Invoice.status != "void")
         .group_by(Client.id).having(func.max(Invoice.date) < cutoff).order_by(func.sum(Invoice.subtotal - Invoice.discount).desc()).limit(100))
    rows = [[n, m, c, a, d, (date.today() - d).days] for n, m, c, a, d in q.all()]
    return dict(columns=[C("n", "Client"), C("m", "Mobile"), C("c", "Visits", "int"), C("a", "Lifetime spend", "money"), C("d", "Last visit", "date"), C("g", "Days away", "int")], rows=rows)


@report("discounts", "sales", "Discounts & coupons given", "Every discount granted - keep leakage under control.")
def r_discounts(R):
    rows = [[i.date, i.number, bname(i.branch_id), i.client.name if i.client else "Guest", i.subtotal, i.discount, i.coupon_code or "Manual", i.created_by.name if i.created_by else ""]
            for i in _inv(R).filter(Invoice.discount > 0).order_by(Invoice.date)]
    return dict(columns=[C("d", "Date", "date"), C("i", "Invoice"), C("b", "Branch"), C("c", "Client"), C("s", "Bill value", "money"), C("x", "Discount", "money"), C("k", "Coupon"), C("u", "Given by")],
                rows=rows, totals=["Total", "", "", "", sum(r[4] for r in rows), sum(r[5] for r in rows), "", ""])


@report("retail_sales", "sales", "Retail product sales", "Products sold over the counter with quantity and revenue.")
def r_retail(R):
    q = (db.session.query(InvoiceItem.description, func.sum(InvoiceItem.qty), func.sum(InvoiceItem.net_amount))
         .join(Invoice, Invoice.id == InvoiceItem.invoice_id).filter(InvoiceItem.kind == "product", Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(R.d1, R.d2), Invoice.status != "void")
         .group_by(InvoiceItem.description).order_by(func.sum(InvoiceItem.net_amount).desc()))
    rows = [[n, q_, a] for n, q_, a in q.all()]
    return dict(columns=[C("n", "Product"), C("q", "Units sold", "int"), C("a", "Revenue", "money")], rows=rows, totals=["Total", sum(r[1] for r in rows), sum(r[2] for r in rows)])


# =============================================================================
# APPOINTMENTS
# =============================================================================
def _ap(R):
    return Appointment.query.filter(Appointment.branch_id.in_(R.branch_ids), Appointment.start_dt >= datetime.combine(R.d1, datetime.min.time()),
                                    Appointment.start_dt < datetime.combine(R.d2 + timedelta(days=1), datetime.min.time()))


@report("appt_summary", "appointments", "Appointments summary", "Bookings by branch, status and booking source (online, phone, walk-in, Instagram...).")
def r_appt_summary(R):
    rows = []
    for b in Branch.query.filter(Branch.id.in_(R.branch_ids)):
        a = _ap(R).filter(Appointment.branch_id == b.id).all()
        n = len(a)
        rows.append([b.name, n, sum(x.status == "completed" for x in a), sum(x.status == "cancelled" for x in a), sum(x.status == "no_show" for x in a),
                     sum(x.source in ("online", "website", "instagram") for x in a), sum(x.source not in ("online", "website", "instagram") for x in a),
                     sum(x.total for x in a if x.status == "completed")])
    return dict(columns=[C("b", "Branch"), C("n", "Bookings", "int"), C("c", "Completed", "int"), C("x", "Cancelled", "int"), C("ns", "No-shows", "int"), C("o", "Online", "int"),
                         C("f", "Offline", "int"), C("v", "Completed value", "money")], rows=rows, totals=["Total"] + [sum(r[i] for r in rows) for i in range(1, 8)])


@report("cancellations", "appointments", "Cancellations & no-shows", "Who cancelled, why, and how much value was lost.")
def r_cancel(R):
    rows = [[a.start_dt.date(), a.start_dt.strftime("%I:%M %p").lstrip("0"), bname(a.branch_id), a.client.name, a.employee.name if a.employee else "—",
             "No-show" if a.status == "no_show" else "Cancelled", a.cancel_reason or "", a.total] for a in _ap(R).filter(Appointment.status.in_(("cancelled", "no_show"))).order_by(Appointment.start_dt)]
    return dict(columns=[C("d", "Date", "date"), C("t", "Time"), C("b", "Branch"), C("c", "Client"), C("e", "Stylist"), C("s", "Status"), C("r", "Reason"), C("v", "Value lost", "money")], rows=rows,
                totals=["Total", "", "", "", "", "", "", sum(r[7] for r in rows)])


@report("utilization", "appointments", "Stylist utilisation", "Booked time vs available time for every appointment-taking employee.")
def r_util(R):
    rows = []
    for e in Employee.query.filter_by(active=True, takes_appointments=True).order_by(Employee.name):
        booked = sum(a.duration for a in _ap(R).filter(Appointment.employee_id == e.id, Appointment.status.in_(("booked", "confirmed", "arrived", "in_service", "completed"))))
        days = sum(1 for d in daterange(R.d1, min(R.d2, date.today() + timedelta(days=60))) if d.weekday() != e.weekly_off and e.location_on(d) in R.branch_ids)
        avail = days * (hhmm_to_min(e.shift_end) - hhmm_to_min(e.shift_start))
        if days:
            rows.append([e.name, bname(e.home_branch_id), days, round(booked / 60, 1), round(avail / 60, 1), _pct(booked, avail)])
    return dict(columns=[C("e", "Stylist"), C("b", "Home branch"), C("d", "Working days", "int"), C("bk", "Booked hrs", "text"), C("av", "Available hrs", "text"), C("u", "Utilisation", "pct")], rows=rows,
                chart=dict(labels=[r[0] for r in rows], series=[dict(name="Utilisation %", values=[r[5] for r in rows])]))


@report("appt_load", "appointments", "Bookings by day", "Number of bookings for every day in the range - see future load too.")
def r_load(R):
    cnt = defaultdict(lambda: [0, 0.0])
    for a in _ap(R).filter(Appointment.status.in_(("booked", "confirmed", "arrived", "in_service", "completed"))):
        cnt[a.start_dt.date()][0] += 1; cnt[a.start_dt.date()][1] += a.total
    rows = [[d, d.strftime("%A"), cnt[d][0], cnt[d][1]] for d in daterange(R.d1, R.d2)]
    return dict(columns=[C("d", "Date", "date"), C("w", "Day"), C("n", "Bookings", "int"), C("v", "Booked value", "money")], rows=rows,
                totals=["Total", "", sum(r[2] for r in rows), sum(r[3] for r in rows)], chart=dict(labels=[r[0].strftime("%d %b") for r in rows[-31:]], series=[dict(name="Bookings", values=[r[2] for r in rows[-31:]])]))


# =============================================================================
# HR
# =============================================================================
@report("attendance_matrix", "hr", "Monthly attendance sheet", "P present · L late · H half-day · A absent · W weekly off · V paid leave · U unpaid leave · O holiday", period=False, month=True)
def r_att(R):
    d1, d2 = month_bounds(R.month)
    cols = [C("e", "Employee")] + [C(f"d{d.day}", str(d.day), "att") for d in daterange(d1, d2)] + [C("p", "Present", "int"), C("l", "Late", "int"), C("a", "Absent", "int")]
    rows = []
    for e in Employee.query.filter(Employee.active == True, Employee.role != "super_admin", Employee.branch_id.in_(R.branch_ids)).order_by(Employee.branch_id, Employee.name):  # noqa: E712
        s = pr.attendance_summary(e, d1, d2)
        rows.append([e.name] + [s["days"][d] for d in daterange(d1, d2)] + [s["present"] + s["half"], s["late"], s["absent"]])
    return dict(columns=cols, rows=rows, wide=True)


@report("punctuality", "hr", "Late marks & early exits", "Punctuality record that feeds salary deductions.")
def r_punct(R):
    rows = []
    for e in Employee.query.filter(Employee.active == True, Employee.role != "super_admin", Employee.branch_id.in_(R.branch_ids)).order_by(Employee.name):  # noqa: E712
        s = pr.attendance_summary(e, R.d1, R.d2)
        late_min = db.session.query(func.coalesce(func.sum(Attendance.late_min), 0)).filter(Attendance.employee_id == e.id, Attendance.date.between(R.d1, R.d2)).scalar()
        rows.append([e.name, bname(e.branch_id), s["late"], late_min, s["early"], s["half"], s["absent"]])
    rows.sort(key=lambda r: -(r[2] + r[4]))
    return dict(columns=[C("e", "Employee"), C("b", "Branch"), C("l", "Late marks", "int"), C("m", "Total late min", "int"), C("x", "Early exits", "int"), C("h", "Half days", "int"), C("a", "Absent", "int")], rows=rows)


@report("payroll_summary", "hr", "Payroll summary", "Salary, deductions, incentives, reimbursements and tips for the selected month.", period=False, month=True)
def r_payroll(R):
    pays = pr.generate_payroll(R.month, R.branch_ids)
    db.session.commit()
    rows = [[p.employee.name, bname(p.branch_id), p.base_salary, p.deduction_amt, p.service_revenue, p.incentive_amt, p.reimb_amt, p.net_salary, p.tips_amt, p.total_payable, "Paid" if p.status == "paid" else "Pending"] for p in pays]
    return dict(columns=[C("e", "Employee"), C("b", "Branch"), C("s", "Base salary", "money"), C("d", "Deductions", "money"), C("r", "Service revenue", "money"), C("i", "Incentive", "money"),
                         C("re", "Reimb.", "money"), C("n", "Net salary", "money"), C("t", "Tips", "money"), C("p", "Total payable", "money"), C("st", "Status")], rows=rows,
                totals=["Total", ""] + [sum(r[i] for r in rows) for i in range(2, 10)] + [""])


@report("leave_balance", "hr", "Leave register", "Approved and pending leaves with the paid/unpaid split.")
def r_leave(R):
    rows = [[l.employee.name, l.kind.title(), l.from_date, l.to_date, l.days, l.status.title(), l.reason or ""] for l in
            LeaveRequest.query.join(Employee, Employee.id == LeaveRequest.employee_id).filter(Employee.branch_id.in_(R.branch_ids), LeaveRequest.from_date <= R.d2, LeaveRequest.to_date >= R.d1).order_by(LeaveRequest.from_date)]
    return dict(columns=[C("e", "Employee"), C("k", "Type"), C("f", "From", "date"), C("t", "To", "date"), C("d", "Days", "int"), C("s", "Status"), C("r", "Reason")], rows=rows)


@report("reimb_report", "hr", "Employee reimbursements", "Out-of-pocket expenses claimed by employees and their settlement status.")
def r_reimb(R):
    rows = [[r.date, r.employee.name, r.description or "", r.category.name if r.category else "", r.amount, "Daily" if r.settle_mode == "daily" else "With salary", r.status.title()] for r in
            Reimbursement.query.join(Employee, Employee.id == Reimbursement.employee_id).filter(Employee.branch_id.in_(R.branch_ids), Reimbursement.date.between(R.d1, R.d2)).order_by(Reimbursement.date)]
    return dict(columns=[C("d", "Date", "date"), C("e", "Employee"), C("n", "Description"), C("h", "Head"), C("a", "Amount", "money"), C("m", "Settle"), C("s", "Status")], rows=rows,
                totals=["Total", "", "", "", sum(r[4] for r in rows), "", ""])


# =============================================================================
# INVENTORY
# =============================================================================
@report("stock_usage", "inventory", "Item-wise usage", "Quantity and value of every item used, sold or wasted - switch between daily, MTD and YTD.")
def r_usage(R):
    q = (db.session.query(Item.name, Item.unit, StockMovement.kind, func.sum(-StockMovement.qty), func.sum(-StockMovement.qty * StockMovement.unit_cost))
         .join(Item, Item.id == StockMovement.item_id).filter(StockMovement.branch_id.in_(R.branch_ids), StockMovement.date.between(R.d1, R.d2),
                                                              StockMovement.kind.in_(("consumption", "sale", "wastage"))).group_by(Item.id, StockMovement.kind))
    d = defaultdict(lambda: dict(unit="", used=0, sold=0, waste=0, value=0))
    for n, u, k, qy, v in q.all():
        r = d[n]; r["unit"] = u; r[{"consumption": "used", "sale": "sold", "wastage": "waste"}[k]] += qy; r["value"] += v
    rows = sorted(([n, r["unit"], r["used"], r["sold"], r["waste"], r["used"] + r["sold"] + r["waste"], r["value"]] for n, r in d.items()), key=lambda x: -x[6])
    return dict(columns=[C("i", "Item"), C("u", "Unit"), C("us", "Used in services", "num"), C("s", "Sold (retail)", "num"), C("w", "Wastage", "num"), C("t", "Total out", "num"), C("v", "Value at cost", "money")], rows=rows,
                totals=["Total", "", "", "", "", "", sum(r[6] for r in rows)], chart=dict(labels=[r[0] for r in rows[:10]], series=[dict(name="Value", values=[r[6] for r in rows[:10]])]))


@report("stock_valuation", "inventory", "Stock on hand & valuation", "Current quantity, reorder level and value of every item at every branch.", period=False)
def r_valuation(R):
    q = StockLevel.query.join(Item, Item.id == StockLevel.item_id).filter(StockLevel.branch_id.in_(R.branch_ids), Item.active == True).order_by(Item.name)  # noqa: E712
    rows = [[s.item.name, s.item.category or "", bname(s.branch_id), s.qty, s.item.unit, s.reorder_level, s.item.cost, s.qty * s.item.cost, "LOW" if s.low else "OK"] for s in q]
    return dict(columns=[C("i", "Item"), C("c", "Category"), C("b", "Branch"), C("q", "On hand", "num"), C("u", "Unit"), C("r", "Reorder at", "num"), C("co", "Cost", "money"), C("v", "Value", "money"), C("s", "Status")],
                rows=rows, totals=["Total", "", "", "", "", "", "", sum(r[7] for r in rows), ""])


@report("low_stock", "inventory", "Low stock & reorder list", "Items at or below their reserve limit.", period=False)
def r_low(R):
    q = StockLevel.query.join(Item, Item.id == StockLevel.item_id).filter(StockLevel.branch_id.in_(R.branch_ids), Item.active == True, StockLevel.qty <= StockLevel.reorder_level).order_by(StockLevel.qty)  # noqa: E712
    rows = [[s.item.name, bname(s.branch_id), s.qty, s.item.unit, s.reorder_level, max(s.reorder_level * 2 - s.qty, 0), s.item.vendor.name if s.item.vendor else ""] for s in q]
    return dict(columns=[C("i", "Item"), C("b", "Branch"), C("q", "On hand", "num"), C("u", "Unit"), C("r", "Reorder at", "num"), C("s", "Suggested order", "num"), C("v", "Usual vendor")], rows=rows)


@report("purchases", "inventory", "Purchase register", "Every stock purchase bill with vendor, value and payment status.")
def r_purch(R):
    rows = [[b.date, b.vendor.name, bname(b.branch_id), b.bill_no or "", b.total, b.paid, b.due] for b in PurchaseBill.query.filter(PurchaseBill.branch_id.in_(R.branch_ids), PurchaseBill.date.between(R.d1, R.d2)).order_by(PurchaseBill.date)]
    return dict(columns=[C("d", "Date", "date"), C("v", "Vendor"), C("b", "Branch"), C("n", "Bill no."), C("t", "Bill value", "money"), C("p", "Paid", "money"), C("du", "Due", "money")], rows=rows,
                totals=["Total", "", "", "", sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows)])


@report("wastage", "inventory", "Wastage & adjustments", "Damaged, expired or adjusted stock - a check on shrinkage.")
def r_waste(R):
    q = StockMovement.query.filter(StockMovement.branch_id.in_(R.branch_ids), StockMovement.date.between(R.d1, R.d2), StockMovement.kind.in_(("wastage", "adjustment"))).order_by(StockMovement.date)
    rows = [[m.date, bname(m.branch_id), m.item.name, m.kind.title(), m.qty, m.item.unit, m.qty * m.unit_cost, m.note or "", m.created_by.name if m.created_by else ""] for m in q]
    return dict(columns=[C("d", "Date", "date"), C("b", "Branch"), C("i", "Item"), C("k", "Type"), C("q", "Qty", "num"), C("u", "Unit"), C("v", "Value", "money"), C("n", "Note"), C("by", "By")], rows=rows,
                totals=["Total", "", "", "", "", "", sum(r[6] for r in rows), "", ""])


# =============================================================================
# MARKETING
# =============================================================================
@report("lead_conversion", "marketing", "Lead sources & conversion", "Which channels bring enquiries and how many turn into bookings.")
def r_leads(R):
    rows = []
    d = defaultdict(lambda: [0, 0, 0])
    for l in Lead.query.filter(Lead.created_at >= datetime.combine(R.d1, datetime.min.time()), Lead.created_at < datetime.combine(R.d2 + timedelta(days=1), datetime.min.time())):
        d[l.source or "Other"][0] += 1
        d[l.source or "Other"][1] += l.status == "booked"
        d[l.source or "Other"][2] += l.status == "lost"
    rows = [[s, v[0], v[1], v[2], _pct(v[1], v[0])] for s, v in sorted(d.items(), key=lambda x: -x[1][0])]
    return dict(columns=[C("s", "Source"), C("n", "Leads", "int"), C("b", "Booked", "int"), C("l", "Lost", "int"), C("c", "Conversion", "pct")], rows=rows,
                totals=["Total", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows), _pct(sum(r[2] for r in rows), sum(r[1] for r in rows))])


@report("booking_sources", "marketing", "New clients by source", "Where new clients heard about you: Instagram, Google, walk-in, referrals...")
def r_sources(R):
    q = (db.session.query(func.coalesce(Client.source, "Unknown"), func.count(Client.id)).filter(Client.created_at >= datetime.combine(R.d1, datetime.min.time()),
                                                                                                   Client.created_at < datetime.combine(R.d2 + timedelta(days=1), datetime.min.time())).group_by(Client.source).order_by(func.count(Client.id).desc()))
    rows = [[s, n] for s, n in q.all()]
    return dict(columns=[C("s", "Source"), C("n", "New clients", "int")], rows=rows, totals=["Total", sum(r[1] for r in rows)], chart=dict(labels=[r[0] for r in rows], series=[dict(name="Clients", values=[r[1] for r in rows])]))


@report("campaign_log", "marketing", "Campaign history", "Broadcasts sent to clients over WhatsApp.", period=False)
def r_campaigns(R):
    rows = [[c.created_at.date(), c.name, c.segment.replace("_", " ").title(), c.status.title(), c.sent_count] for c in Campaign.query.order_by(Campaign.created_at.desc())]
    return dict(columns=[C("d", "Date", "date"), C("n", "Campaign"), C("s", "Audience"), C("st", "Status"), C("c", "Messages sent", "int")], rows=rows)


@report("coupon_usage", "marketing", "Coupon usage", "Redemptions and discount value by coupon.", period=False)
def r_coupons(R):
    rows = []
    for c in Coupon.query.order_by(Coupon.code):
        disc = db.session.query(func.coalesce(func.sum(Invoice.discount), 0)).filter(func.upper(Invoice.coupon_code) == c.code.upper(), Invoice.status != "void").scalar()
        rows.append([c.code, c.description or "", f"{c.value:g}{'%' if c.kind == 'percent' else ' off'}", c.used_count, disc, "Active" if c.active else "Off"])
    return dict(columns=[C("c", "Code"), C("d", "Description"), C("v", "Value"), C("u", "Used", "int"), C("x", "Discount given", "money"), C("s", "Status")], rows=rows)


@report("feedback_report", "marketing", "Client feedback & ratings", "Average rating by branch and employee, with low-rating follow-ups.")
def r_feedback(R):
    q = Feedback.query.filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(R.branch_ids), func.date(Feedback.submitted_at).between(R.d1, R.d2))
    rows = [[f.submitted_at.date(), bname(f.branch_id), f.client.name if f.client else "", f.employee.name if f.employee else "", "★" * f.rating + "☆" * (5 - f.rating), f.comment or ""] for f in q.order_by(Feedback.submitted_at.desc())]
    avg = round(sum(f.rating for f in q) / q.count(), 2) if q.count() else 0
    return dict(columns=[C("d", "Date", "date"), C("b", "Branch"), C("c", "Client"), C("e", "Stylist"), C("r", "Rating"), C("m", "Comment")], rows=rows,
                kpis=[("Average rating", avg, "num"), ("Responses", q.count(), "int")])


# =============================================================================
# OWNER MIS
# =============================================================================
@report("target_vs_actual", "mis", "Target vs actual", "Monthly revenue targets against achievement for every branch and employee.", period=False)
def r_target(R):
    d1, d2 = date.today().replace(day=1), date.today()
    rows = []
    for b in Branch.query.filter(Branch.id.in_(R.branch_ids)):
        act = fin.pnl([b.id], d1, d2)["total_revenue"]
        rows.append([b.name, "Branch", b.monthly_target, act, _pct(act, b.monthly_target)])
    for e in Employee.query.filter(Employee.active == True, Employee.monthly_target > 0, Employee.branch_id.in_(R.branch_ids)).order_by(Employee.name):  # noqa: E712
        act = pr.service_revenue(e.id, d1, d2)
        rows.append([e.name, "Employee · " + bname(e.branch_id), e.monthly_target, act, _pct(act, e.monthly_target)])
    return dict(columns=[C("n", "Name"), C("t", "Level"), C("tg", "Monthly target", "money"), C("a", "Achieved (MTD)", "money"), C("p", "% achieved", "pct")], rows=rows,
                note="Targets are set in Settings → Branches and on each employee's profile.")


@report("owner_scorecard", "mis", "Owner scorecard", "One-page health check: income, profit, clients, bookings, ratings and staff attendance versus the previous period.")
def r_scorecard(R):
    span = (R.d2 - R.d1).days + 1
    p1, p2 = R.d1 - timedelta(days=span), R.d1 - timedelta(days=1)
    def kp(a, b):
        P = fin.pnl(R.branch_ids, a, b)
        inv = Invoice.query.filter(Invoice.branch_id.in_(R.branch_ids), Invoice.date.between(a, b), Invoice.status != "void")
        n = inv.count()
        clients = len({i.client_id for i in inv if i.client_id})
        S = fin.summary(R.branch_ids, a, b)
        ap = Appointment.query.filter(Appointment.branch_id.in_(R.branch_ids), Appointment.start_dt >= datetime.combine(a, datetime.min.time()), Appointment.start_dt < datetime.combine(b + timedelta(days=1), datetime.min.time()))
        canc = ap.filter(Appointment.status.in_(("cancelled", "no_show"))).count()
        return dict(rev=P["total_revenue"], profit=P["net_profit"], exp=P["total_expense"], bills=n, avg=P["total_revenue"] / n if n else 0, clients=clients, tips=S["tips_in"], appts=ap.count(), canc=_pct(canc, ap.count()))
    a, b = kp(R.d1, R.d2), kp(p1, p2)
    from ..helpers import inr as _inr

    def fmt(v, ty):
        return _inr(v) if ty == "money" else (f"{v:.1f}%" if ty == "pct" else f"{v:,.0f}")

    def row(label, k, ty):
        ch = _pct(a[k] - b[k], b[k]) if b[k] else 0
        return [label, fmt(a[k], ty), fmt(b[k], ty), ch]
    rows = [row("Business income", "rev", "money"), row("Expenses", "exp", "money"), row("Net profit", "profit", "money"), row("Bills raised", "bills", "int"), row("Average ticket", "avg", "money"),
            row("Unique clients", "clients", "int"), row("Tips collected", "tips", "money"), row("Appointments", "appts", "int"), row("Cancellation / no-show %", "canc", "pct")]
    return dict(columns=[C("m", "Metric"), C("c", "This period", "num"), C("p", "Previous period", "num"), C("g", "Change %", "pct")], rows=rows,
                note=f"Compared with the previous {span} day(s): {p1.strftime('%d %b')} – {p2.strftime('%d %b %Y')}.", kpis=[("Income", a["rev"], "money"), ("Net profit", a["profit"], "money"), ("Bills", a["bills"], "int")])


def run(key, R):
    meta = REPORTS[key]
    out = meta["fn"](R)
    out.setdefault("rows", []); out.setdefault("kpis", []); out.setdefault("totals", None); out.setdefault("chart", None); out.setdefault("note", "")
    return meta, out
