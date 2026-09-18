"""WhatsApp messaging layer.

Every message the business sends goes through `queue()`.  In *simulated* mode (default) the message
is stored in the WhatsApp Outbox with its full text so the owner can see exactly what clients and
employees would receive.  To go live, set the provider in Settings and implement `_deliver()` for
the chosen WhatsApp Business API (Meta Cloud API / Twilio / Gupshup) - nothing else in the app changes.
"""
from datetime import datetime, timedelta
from urllib.parse import quote

from ..extensions import db
from ..helpers import fmt_date, fmt_time, inr, setting, setting_num
from ..models import Appointment, WhatsAppMessage

TEMPLATES = {
    "appt_confirm": ("Appointment confirmed",
        "Hi {client}! ✅ Your appointment at *RK Hair Pro – {branch}* is confirmed.\n"
        "📅 {date} at {time}\n💇 {services}\n👤 With {employee}\n💰 Estimated {amount}\n"
        "🔖 Booking ID: {code}\n📍 {address}\n\nNeed a change? Call us on {phone}. See you soon!"),
    "appt_update": ("Appointment updated",
        "Hi {client}, your appointment at *RK Hair Pro – {branch}* has been updated.\n"
        "📅 New slot: {date} at {time}\n💇 {services}\n👤 With {employee}\n"
        "🔖 Booking ID: {code}\n{change_note}\nIf this doesn't suit you, please call {phone}."),
    "appt_cancel": ("Appointment cancelled",
        "Hi {client}, your appointment at *RK Hair Pro – {branch}* on {date} at {time} "
        "(ID {code}) has been cancelled.{reason_line}\nWe'd love to host you another day – "
        "book again anytime: {book_link}"),
    "appt_reminder": ("Appointment reminder (1 hour)",
        "Hi {client}, a gentle reminder ⏰ Your appointment at *RK Hair Pro – {branch}* is today at {time}.\n"
        "💇 {services}\n👤 With {employee}\n📍 {address}\nSee you shortly!"),
    "payment_thanks": ("Payment received – thank you",
        "Thank you {client}! 🙏 We've received your payment of *{paid}* at *RK Hair Pro – {branch}*.\n"
        "🧾 Invoice {invoice} · {date}\n💇 {services}\n{tip_line}💳 Paid via: {modes}\n{due_line}"
        "We hope you loved your experience! Rate us in 10 seconds: {feedback_link}"),
    "tip_paid": ("Tip payout to employee",
        "Hi {employee}, your tips of *{amount}* have been paid ({mode}) on {date}. {balance_line}"
        "Thank you for the great service! – RK Hair Pro"),
    "salary_paid": ("Salary payment",
        "Hi {employee}, your salary for *{month}* has been paid via {mode} on {date}.\n"
        "Base salary: {base}\nDeductions: {deductions}\nIncentive ({pct}%): {incentive}\n"
        "Reimbursements: {reimb}\nTips paid: {tips}\n*Total credited: {total}*\n– RK Hair Pro"),
    "reimb_decision": ("Reimbursement update",
        "Hi {employee}, your reimbursement request of {amount} ({description}) has been *{status}*.{extra}"),
    "reimb_paid": ("Reimbursement paid",
        "Hi {employee}, your reimbursement of *{amount}* ({description}) has been paid via {mode} on {date}. – RK Hair Pro"),
    "leave_decision": ("Leave decision",
        "Hi {employee}, your leave request for {dates} has been *{status}*.{extra}"),
    "role_decision": ("Role change decision",
        "Hi {employee}, your role change request ({from_role} → {to_role}) has been *{status}*."),
    "low_stock": ("Low stock alert (to branch manager)",
        "⚠️ Low stock at {branch}: *{item}* is down to {qty} {unit} (reorder level {level}). "
        "Please raise a stock request in the RK Hair Pro app."),
    "stock_decision": ("Stock request decision",
        "Your stock request for {qty} {unit} of *{item}* ({branch}) has been *{status}*.{extra}"),
    "birthday": ("Birthday greeting",
        "Happy Birthday {client}! 🎂✨ Team RK Hair Pro wishes you a fabulous year ahead. "
        "Enjoy {offer} on your next visit this month. Book: {book_link}"),
    "feedback": ("Feedback request",
        "Hi {client}, thank you for visiting RK Hair Pro – {branch}. How was your experience? {feedback_link}"),
    "campaign": ("Campaign broadcast", "{message}"),
    "announcement": ("Announcement", "📢 {title}\n{body}\n– RK Hair Pro Management"),
}


class _Safe(dict):
    def __missing__(self, k):
        return ""


def template_text(key):
    return setting("tpl_" + key, None) or TEMPLATES[key][1]


def render(key, **ctx):
    return template_text(key).format_map(_Safe(ctx))


def wa_link(mobile, body):
    return f"https://wa.me/91{mobile}?text={quote(body or '')}"


def _deliver(msg):
    """Hook for a real provider. Return True when delivered."""
    return False


def queue(kind, mobile, name, body, branch_id=None):
    mobile = "".join(c for c in (mobile or "") if c.isdigit())[-10:]
    live = setting("whatsapp_mode") == "live"
    status = "simulated"
    m = WhatsAppMessage(to_name=name, to_mobile=mobile, kind=kind, body=body, branch_id=branch_id, status=status)
    if live:
        m.status = "sent" if _deliver(m) else "failed"
    db.session.add(m)
    return m


# ----------------------------------------------------------------------------
# message builders
# ----------------------------------------------------------------------------
def _appt_ctx(a):
    return dict(
        client=a.client.name, branch=a.branch.name, date=fmt_date(a.start_dt, "%a, %d %b %Y"),
        time=fmt_time(a.start_dt), services=a.service_names or "Salon service",
        employee=a.employee.name if a.employee else "our stylist", amount=inr(a.total), code=a.code,
        address=a.branch.address or "", phone=a.branch.phone or "", book_link=book_link())


def base_url():
    """Public address used inside WhatsApp messages: Settings → Contact → Website address, else the address being visited, else localhost."""
    from flask import has_request_context, request
    u = (setting("website") or "").strip()
    if u:
        return u if u.startswith("http") else "https://" + u.strip("/")
    if has_request_context():
        return request.host_url
    return "http://127.0.0.1:5000"


def book_link():
    return base_url().rstrip("/") + "/book"


def appointment_confirmed(a):
    return queue("appt_confirm", a.client.mobile, a.client.name, render("appt_confirm", **_appt_ctx(a)), a.branch_id)


def appointment_updated(a, change_note=""):
    ctx = _appt_ctx(a)
    ctx["change_note"] = change_note
    return queue("appt_update", a.client.mobile, a.client.name, render("appt_update", **ctx), a.branch_id)


def appointment_cancelled(a):
    ctx = _appt_ctx(a)
    ctx["reason_line"] = f" Reason: {a.cancel_reason}." if a.cancel_reason else ""
    return queue("appt_cancel", a.client.mobile, a.client.name, render("appt_cancel", **ctx), a.branch_id)


def appointment_reminder(a):
    return queue("appt_reminder", a.client.mobile, a.client.name, render("appt_reminder", **_appt_ctx(a)), a.branch_id)


def payment_thanks(inv, pay_rows, feedback_token=None, paid_now=None):
    """pay_rows: list of (mode_label, amount)."""
    services = ", ".join(i.description for i in inv.items if i.description) or "Salon services"
    tip_line = f"💝 Tip included: {inr(inv.tip_total)}\n" if inv.tip_total else ""
    modes = ", ".join(f"{m} {inr(a)}" for m, a in pay_rows) or "—"
    due_line = f"⚠️ Balance due: {inr(inv.due)}\n" if inv.due > 0.5 else ""
    link = f"{base_url().rstrip('/')}/feedback/{feedback_token}" if feedback_token else book_link()
    body = render("payment_thanks", client=inv.client.name if inv.client else "Guest", paid=inr(paid_now if paid_now is not None else inv.paid_amount),
                  branch=inv.branch.name, invoice=inv.number, date=fmt_date(inv.date), services=services,
                  tip_line=tip_line, modes=modes, due_line=due_line, feedback_link=link)
    if inv.client:
        return queue("payment_thanks", inv.client.mobile, inv.client.name, body, inv.branch_id)


def tip_paid(emp, amount, balance_after, mode_label, on_date, branch_id=None):
    bl = f"Remaining tip balance: {inr(balance_after)}. " if balance_after > 0.5 else "Your tip balance is now nil. "
    body = render("tip_paid", employee=emp.name, amount=inr(amount), mode=mode_label, date=fmt_date(on_date), balance_line=bl)
    return queue("tip_paid", emp.mobile, emp.name, body, branch_id or emp.branch_id)


def salary_paid(p, mode_label, tips_paid):
    body = render("salary_paid", employee=p.employee.name, month=fmt_date(datetime.strptime(p.month + "-01", "%Y-%m-%d"), "%B %Y"),
                  mode=mode_label, date=fmt_date(p.paid_on), base=inr(p.base_salary), deductions=inr(p.deduction_amt),
                  pct=int(p.incentive_pct or 0), incentive=inr(p.incentive_amt), reimb=inr(p.reimb_amt),
                  tips=inr(tips_paid), total=inr(p.net_salary + tips_paid))
    return queue("salary_paid", p.employee.mobile, p.employee.name, body, p.branch_id)


def simple(kind, emp_or_client, **ctx):
    body = render(kind, **ctx)
    branch_id = getattr(emp_or_client, "branch_id", None)
    return queue(kind, emp_or_client.mobile, emp_or_client.name, body, branch_id)


# ----------------------------------------------------------------------------
# scheduler job
# ----------------------------------------------------------------------------
def send_due_reminders():
    """WhatsApp reminder ~1 hour before every upcoming appointment."""
    window = int(setting_num("reminder_minutes", 60))
    now_ = datetime.now()
    upto = now_ + timedelta(minutes=window)
    sent = 0
    q = Appointment.query.filter(Appointment.status.in_(("booked", "confirmed")), Appointment.reminder_sent == False,  # noqa: E712
                                 Appointment.start_dt > now_, Appointment.start_dt <= upto)
    due = q.all()
    for a in due:
        a.reminder_sent = True
        # booked inside the reminder window -> the confirmation itself is the reminder
        if a.created_at and a.created_at <= a.start_dt - timedelta(minutes=window):
            appointment_reminder(a)
            sent += 1
    if due:
        db.session.commit()
    return sent
