"""HR engine: attendance capture, monthly attendance summary, payroll and reimbursements."""
from datetime import date, datetime, timedelta

from ..extensions import db
from ..helpers import daterange, hhmm_to_min, inr, month_bounds, notify, setting_num
from ..models import Attendance, Employee, Holiday, Invoice, InvoiceItem, LeaveRequest, Payroll, Reimbursement
from . import finance as fin
from . import whatsapp as wa


# ----------------------------------------------------------------------------
# attendance capture (face kiosk / manual)
# ----------------------------------------------------------------------------
def _mins(dt):
    return dt.hour * 60 + dt.minute


def record_scan(emp, when=None, method="face", snapshot=None):
    """First scan of the day = check-in, later scans = check-out. Returns (attendance, 'in'|'out'|'dup')."""
    when = when or datetime.now()
    grace = int(setting_num("grace_minutes", 15))
    att = Attendance.query.filter_by(employee_id=emp.id, date=when.date()).first()
    if att is None or att.check_in is None:
        att = att or Attendance(employee_id=emp.id, date=when.date())
        att.check_in, att.method, att.snapshot = when, method, snapshot
        late = _mins(when) - hhmm_to_min(emp.shift_start or "10:00")
        att.late_min = late if late > grace else 0
        att.status = "present"
        db.session.add(att)
        return att, "in"
    if (when - att.check_in).total_seconds() < 300:
        return att, "dup"
    att.check_out = when
    set_out_metrics(att, emp)
    return att, "out"


def set_out_metrics(att, emp):
    grace = int(setting_num("grace_minutes", 15))
    att.worked_min = int((att.check_out - att.check_in).total_seconds() // 60)
    early = hhmm_to_min(emp.shift_end or "21:00") - _mins(att.check_out)
    att.early_min = early if early > grace else 0
    full, half = setting_num("full_day_min_hours", 7) * 60, setting_num("half_day_min_hours", 4) * 60
    att.status = "present" if att.worked_min >= full else ("half_day" if att.worked_min >= half else "absent")


# ----------------------------------------------------------------------------
# monthly summary
# ----------------------------------------------------------------------------
def leave_days_map(emp_id, d1, d2):
    """{date: 'paid'|'unpaid'} for approved leave, honouring the monthly paid-leave quota."""
    quota = int(setting_num("paid_leaves_per_month", 1))
    used = {}
    out = {}
    q = LeaveRequest.query.filter(LeaveRequest.employee_id == emp_id, LeaveRequest.status == "approved",
                                  LeaveRequest.to_date >= d1, LeaveRequest.from_date <= d2).order_by(LeaveRequest.from_date)
    for lr in q:
        for d in daterange(max(lr.from_date, d1), min(lr.to_date, d2)):
            key = (d.year, d.month)
            if lr.kind == "unpaid":
                out[d] = "unpaid"
            elif used.get(key, 0) < quota:
                used[key] = used.get(key, 0) + 1
                out[d] = "paid"
            else:
                out[d] = "unpaid"
    return out


def attendance_summary(emp, d1, d2, today=None):
    today = today or date.today()
    start = max(d1, emp.joined_on or d1)
    end = min(d2, today)
    recs = {a.date: a for a in Attendance.query.filter(Attendance.employee_id == emp.id, Attendance.date.between(d1, d2))}
    leaves = leave_days_map(emp.id, d1, d2)
    hol = {h.date for h in Holiday.query.filter(Holiday.date.between(d1, d2),
                                                (Holiday.branch_id == emp.home_branch_id) | (Holiday.branch_id.is_(None)))}
    days, c = {}, dict(present=0, late=0, early=0, half=0, absent=0, paid_leave=0, unpaid_leave=0, weekoff=0, holiday=0, worked_min=0)
    for d in daterange(d1, d2):
        if d < start or d > end:
            days[d] = "-"
            continue
        a = recs.get(d)
        if d.weekday() == emp.weekly_off and not (a and a.check_in):
            days[d] = "W"; c["weekoff"] += 1
        elif d in hol and not (a and a.check_in):
            days[d] = "O"; c["holiday"] += 1
        elif a and a.status == "leave":
            kind = leaves.get(d, "paid")
            days[d] = "V" if kind == "paid" else "U"
            c["paid_leave" if kind == "paid" else "unpaid_leave"] += 1
        elif a and a.check_in:
            c["worked_min"] += a.worked_min or 0
            if a.status == "half_day":
                days[d] = "H"; c["half"] += 1
            elif a.status == "absent":
                days[d] = "A"; c["absent"] += 1
            else:
                days[d] = "L" if a.late_min else "P"
                c["present"] += 1
                c["late"] += 1 if a.late_min else 0
            if a.early_min:
                c["early"] += 1
        elif d in leaves:
            days[d] = "V" if leaves[d] == "paid" else "U"
            c["paid_leave" if leaves[d] == "paid" else "unpaid_leave"] += 1
        elif d == today:
            days[d] = "-"          # today not yet marked
        else:
            days[d] = "A"; c["absent"] += 1
    c["days"] = days
    return c


# ----------------------------------------------------------------------------
# payroll
# ----------------------------------------------------------------------------
def service_revenue(emp_id, d1, d2):
    v = (db.session.query(db.func.coalesce(db.func.sum(InvoiceItem.net_amount), 0)).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
         .filter(InvoiceItem.employee_id == emp_id, InvoiceItem.kind == "service", Invoice.status != "void",
                 Invoice.date.between(d1, d2)).scalar())
    return round(v or 0, 2)


def compute_payroll(emp, ym, today=None, tips_upto=None):
    d1, d2 = month_bounds(ym)
    s = attendance_summary(emp, d1, d2, today)
    basis = setting_num("salary_days_basis", 30) or 30
    per_day = round((emp.salary or 0) / basis, 2)
    ded_days = (s["absent"] + s["unpaid_leave"] + 0.5 * s["half"]
                + 0.5 * (s["late"] // int(setting_num("late_marks_per_halfday", 3)))
                + 0.5 * (s["early"] // int(setting_num("early_exits_per_halfday", 3))))
    ded_amt = min(emp.salary or 0, round(ded_days * per_day, 2))
    rev = service_revenue(emp.id, d1, d2)
    inc = round(rev * (emp.commission_pct or 0) / 100.0, 2)
    reimb = sum(r.amount for r in Reimbursement.query.filter_by(employee_id=emp.id, status="approved", settle_mode="salary"))
    tips = fin.tip_balance(emp.id, upto=tips_upto) if emp.tip_mode == "salary" else 0.0
    return dict(base_salary=emp.salary or 0, per_day=per_day, present_days=s["present"], absent_days=s["absent"], half_days=s["half"],
                late_marks=s["late"], early_exits=s["early"], unpaid_leave_days=s["unpaid_leave"], paid_leave_days=s["paid_leave"],
                week_offs=s["weekoff"], deduction_days=ded_days, deduction_amt=ded_amt, service_revenue=rev,
                incentive_pct=emp.commission_pct or 0, incentive_amt=inc, reimb_amt=round(reimb, 2), tips_amt=round(tips, 2),
                net_salary=round((emp.salary or 0) - ded_amt + inc + reimb, 2),
                total_payable=round((emp.salary or 0) - ded_amt + inc + reimb + tips, 2))


def generate_payroll(ym, branch_ids):
    out = []
    q = Employee.query.filter(Employee.active == True, Employee.role != "super_admin")  # noqa: E712
    for e in q.filter(Employee.branch_id.in_(branch_ids)).order_by(Employee.branch_id, Employee.name):
        p = Payroll.query.filter_by(employee_id=e.id, month=ym).first()
        if p and p.status == "paid":
            out.append(p)
            continue
        vals = compute_payroll(e, ym)
        if not p:
            p = Payroll(employee_id=e.id, month=ym, branch_id=e.branch_id)
            db.session.add(p)
        for k, v in vals.items():
            setattr(p, k, v)
        out.append(p)
    db.session.flush()
    return out


def pay_payroll(p, mode, account_id, on_date, by_id=None):
    emp = p.employee
    vals = compute_payroll(emp, p.month, tips_upto=on_date)          # refresh right before paying
    for k, v in vals.items():
        setattr(p, k, v)
    acct = fin.resolve_account(p.branch_id, mode, account_id)
    batch = f"PAY-{p.id}"
    base_net = round(p.base_salary - p.deduction_amt, 2)
    if base_net > 0:
        fin.record("out", base_net, fin.cat(fin.H_SALARY), acct, on_date, p.branch_id, mode=mode, party=emp.name, employee_id=emp.id,
                   payroll_id=p.id, batch=batch, notes=f"Salary {p.month}", created_by_id=by_id)
    if p.incentive_amt > 0:
        fin.record("out", p.incentive_amt, fin.cat(fin.H_INCENTIVE), acct, on_date, p.branch_id, mode=mode, party=emp.name,
                   employee_id=emp.id, payroll_id=p.id, batch=batch, notes=f"Incentive {p.month} ({p.incentive_pct:g}% of {inr(p.service_revenue)})",
                   created_by_id=by_id)
    for r in Reimbursement.query.filter_by(employee_id=emp.id, status="approved", settle_mode="salary").all():
        fin.record("out", r.amount, r.category or fin.cat(fin.H_REIMB), acct, on_date, p.branch_id, mode=mode, party=emp.name,
                   employee_id=emp.id, payroll_id=p.id, batch=batch, notes=f"Reimbursement: {r.description}", created_by_id=by_id)
        r.status, r.paid_on = "paid", on_date
    tips_paid = 0.0
    if p.tips_amt > 0:
        fin.pay_tips(emp, p.tips_amt, mode, acct.id, p.branch_id, on_date, by_id, note=f"Accumulated tips paid with salary {p.month}", silent=True)
        tips_paid = p.tips_amt
    p.status, p.paid_on, p.mode, p.account_id = "paid", on_date, mode, acct.id
    db.session.flush()
    wa.salary_paid(p, fin.MODE_LABEL.get(mode, mode), tips_paid)
    notify(emp.id, "Salary credited", f"{inr(p.net_salary + tips_paid)} for {p.month} via {fin.MODE_LABEL.get(mode, mode)}", "/me/earnings", "money")


def pay_reimbursement(r, mode, account_id, on_date, by_id=None):
    acct = fin.resolve_account(r.branch_id or r.employee.branch_id, mode, account_id)
    fin.record("out", r.amount, r.category or fin.cat(fin.H_REIMB), acct, on_date, r.branch_id or r.employee.branch_id, mode=mode,
               party=r.employee.name, employee_id=r.employee_id, notes=f"Reimbursement: {r.description}", created_by_id=by_id)
    r.status, r.paid_on = "paid", on_date
    wa.simple("reimb_paid", r.employee, employee=r.employee.name, amount=inr(r.amount), description=r.description,
              mode=fin.MODE_LABEL.get(mode, mode), date=on_date.strftime("%d %b %Y"))
    notify(r.employee_id, "Reimbursement paid", f"{inr(r.amount)} paid via {fin.MODE_LABEL.get(mode, mode)}", "/me/earnings", "money")
