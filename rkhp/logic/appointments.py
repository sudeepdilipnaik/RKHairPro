"""Appointment engine: availability, slot generation, conflict-safe booking."""
import random
import string
from datetime import date, datetime, timedelta

from ..extensions import db
from ..helpers import hhmm_to_min, setting_num
from ..models import (ACTIVE_APPT, Appointment, AppointmentService, Attendance, BlockedSlot, Branch, Client, Employee,
                      Holiday, LeaveRequest, Service)


def bookable_employees(branch_id, on_date, public_only=False):
    """Employees who take appointments and are physically at this branch on this date."""
    q = Employee.query.filter_by(active=True, takes_appointments=True)
    if public_only:
        q = q.filter_by(public_visible=True)
    out = []
    for e in q.order_by(Employee.role != "super_admin", Employee.name).all():
        if e.location_on(on_date) != branch_id:
            continue
        if on_date.weekday() == e.weekly_off:
            continue
        if on_leave(e.id, on_date):
            continue
        out.append(e)
    return out


def on_leave(emp_id, d):
    return LeaveRequest.query.filter(LeaveRequest.employee_id == emp_id, LeaveRequest.status == "approved",
                                     LeaveRequest.from_date <= d, LeaveRequest.to_date >= d).first() is not None


def is_holiday(branch_id, d):
    return Holiday.query.filter(Holiday.date == d, (Holiday.branch_id == branch_id) | (Holiday.branch_id.is_(None))).first()


def branch_window(branch, d):
    o, c = hhmm_to_min(branch.open_time), hhmm_to_min(branch.close_time)
    base = datetime.combine(d, datetime.min.time())
    return base + timedelta(minutes=o), base + timedelta(minutes=c)


def is_busy(emp_id, start, end, exclude_id=None):
    q = Appointment.query.filter(Appointment.employee_id == emp_id, Appointment.status.in_(ACTIVE_APPT),
                                 Appointment.start_dt < end, Appointment.end_dt > start)
    if exclude_id:
        q = q.filter(Appointment.id != exclude_id)
    if q.first():
        return True
    return BlockedSlot.query.filter(BlockedSlot.employee_id == emp_id, BlockedSlot.start_dt < end, BlockedSlot.end_dt > start).first() is not None


def free_slots(branch_id, d, duration, employee_id=None, exclude_id=None, public=False, now_dt=None):
    """Return ([{'time': '10:00', 'label': '10:00 AM', 'employees': [ids]}], employees_list)."""
    branch = db.session.get(Branch, branch_id)
    if not branch or not branch.active or is_holiday(branch_id, d):
        return [], []
    emps = bookable_employees(branch_id, d, public_only=public)
    if employee_id:
        emps = [e for e in emps if e.id == int(employee_id)]
    if not emps:
        return [], []
    open_dt, close_dt = branch_window(branch, d)
    step = branch.slot_minutes or 30
    now_dt = now_dt or datetime.now()
    min_notice = timedelta(minutes=setting_num("booking_min_notice_min", 30)) if public else timedelta(0)
    slots = []
    t = open_dt
    dur = max(int(duration or step), step)
    while t + timedelta(minutes=dur) <= close_dt:
        end = t + timedelta(minutes=dur)
        if t >= now_dt + min_notice or (not public and t.date() > now_dt.date()):
            free = [e.id for e in emps if _within_shift(e, t, end) and not is_busy(e.id, t, end, exclude_id)]
            if free:
                slots.append({"time": t.strftime("%H:%M"), "label": t.strftime("%I:%M %p").lstrip("0"), "employees": free})
        t += timedelta(minutes=step)
    return slots, emps


def _within_shift(e, start, end):
    s, en = hhmm_to_min(e.shift_start or "00:00"), hhmm_to_min(e.shift_end or "23:59")
    sm = start.hour * 60 + start.minute
    em = end.hour * 60 + end.minute
    return sm >= s and em <= en


def gen_code():
    while True:
        code = "RK" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not Appointment.query.filter_by(code=code).first():
            return code


def find_or_create_client(name, mobile, source=None, branch_id=None, email=None, gender=None):
    mobile = "".join(c for c in mobile if c.isdigit())[-10:]
    c = Client.query.filter_by(mobile=mobile).first()
    if c:
        if name and (not c.name or c.name.lower() == "guest"):
            c.name = name
        return c
    c = Client(name=name or "Guest", mobile=mobile, source=source, branch_id=branch_id, email=email, gender=gender)
    db.session.add(c)
    db.session.flush()
    return c


class BookingError(Exception):
    pass


def create_appointment(branch_id, client, start_dt, service_ids, employee_id=None, source="offline", notes=None,
                       created_by_id=None, public=False, status="booked", override_conflict=False):
    services = Service.query.filter(Service.id.in_(service_ids), Service.active == True).all()  # noqa: E712
    if not services:
        raise BookingError("Please choose at least one service.")
    duration = sum(s.duration for s in services)
    end_dt = start_dt + timedelta(minutes=duration)
    d = start_dt.date()
    if d < date.today() and not override_conflict:
        raise BookingError("That date is in the past.")
    limit = int(setting_num("booking_advance_days", 120))
    if public and d > date.today() + timedelta(days=limit):
        raise BookingError(f"Online booking is open up to {limit} days ahead.")
    slots, _ = free_slots(branch_id, d, duration, employee_id, public=public)
    ok = next((s for s in slots if s["time"] == start_dt.strftime("%H:%M")), None)
    if not ok and not override_conflict:
        raise BookingError("Sorry, that slot has just been taken. Please choose another time.")
    if ok:
        if employee_id and int(employee_id) not in ok["employees"]:
            raise BookingError("The chosen stylist is not available at that time.")
        if not employee_id:
            employee_id = _least_busy(ok["employees"], d)
    a = Appointment(code=gen_code(), branch_id=branch_id, client_id=client.id, employee_id=int(employee_id) if employee_id else None,
                    start_dt=start_dt, end_dt=end_dt, status=status, source=source, notes=notes, created_by_id=created_by_id)
    for s in services:
        a.lines.append(AppointmentService(service_id=s.id, price=s.price, duration=s.duration))
    db.session.add(a)
    db.session.flush()
    return a


def reschedule(a, start_dt, service_ids=None, employee_id=None):
    """Move / modify an existing appointment.  Returns a human-readable change note."""
    services = Service.query.filter(Service.id.in_(service_ids)).all() if service_ids else [l.service for l in a.lines]
    if not services:
        raise BookingError("Please choose at least one service.")
    duration = sum(s.duration for s in services)
    d = start_dt.date()
    slots, _ = free_slots(a.branch_id, d, duration, employee_id, exclude_id=a.id)
    ok = next((s for s in slots if s["time"] == start_dt.strftime("%H:%M")), None)
    if not ok:
        raise BookingError("That slot is not available for the chosen stylist and services.")
    if not employee_id:
        employee_id = _least_busy(ok["employees"], d)
    changes = []
    if a.start_dt != start_dt:
        changes.append(f"time moved from {a.start_dt.strftime('%d %b, %I:%M %p')} to {start_dt.strftime('%d %b, %I:%M %p')}")
    if a.employee_id != int(employee_id):
        new_e = db.session.get(Employee, int(employee_id))
        changes.append(f"stylist changed to {new_e.name}")
    if set(l.service_id for l in a.lines) != set(s.id for s in services):
        changes.append("services updated")
        for l in list(a.lines):
            db.session.delete(l)
        a.lines = [AppointmentService(service_id=s.id, price=s.price, duration=s.duration) for s in services]
    a.start_dt, a.end_dt = start_dt, start_dt + timedelta(minutes=duration)
    a.employee_id = int(employee_id)
    a.reminder_sent = False
    return "; ".join(changes).capitalize() + "." if changes else ""


def _least_busy(emp_ids, d):
    start = datetime.combine(d, datetime.min.time())
    counts = {}
    for i in emp_ids:
        counts[i] = Appointment.query.filter(Appointment.employee_id == i, Appointment.status.in_(ACTIVE_APPT),
                                             Appointment.start_dt >= start, Appointment.start_dt < start + timedelta(days=1)).count()
    return min(emp_ids, key=lambda i: (counts[i], i))


def day_appointments(branch_ids, d, employee_id=None, include_cancelled=True):
    start = datetime.combine(d, datetime.min.time())
    q = Appointment.query.filter(Appointment.branch_id.in_(branch_ids), Appointment.start_dt >= start,
                                 Appointment.start_dt < start + timedelta(days=1))
    if employee_id:
        q = q.filter(Appointment.employee_id == employee_id)
    if not include_cancelled:
        q = q.filter(Appointment.status.in_(ACTIVE_APPT))
    return q.order_by(Appointment.start_dt).all()
