from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import (all_branches, audit, clean_mobile, form_branch_id, guard_branch, hhmm_to_min, parse_date, require_perm, scope_ids, to_float,
                       to_int, manager_only)
from ..logic import appointments as al
from ..logic import whatsapp as wa
from ..models import (APPT_STATUS, Appointment, BlockedSlot, Branch, Client, Employee, EmployeeDayBranch, Item, Service, ServiceCategory,
                      ServiceConsumable)

bp = Blueprint("appt", __name__, url_prefix="/appointments")


def _branch_for_view():
    """Branch shown on the board: ?b= (must be accessible) else user's/active branch else first branch."""
    ids = scope_ids()
    b = to_int(request.args.get("b"))
    if b and b in ids:
        return b
    return ids[0]


@bp.route("/")
@require_perm("appointments")
def board():
    d = parse_date(request.args.get("date"), date.today())
    ids = scope_ids()
    bid = _branch_for_view()
    br = db.session.get(Branch, bid)
    start_dt = datetime.combine(d, datetime.min.time())
    appts = Appointment.query.filter(Appointment.branch_id == bid, Appointment.start_dt >= start_dt, Appointment.start_dt < start_dt + timedelta(days=1)).order_by(Appointment.start_dt).all()
    emps = al.bookable_employees(bid, d)
    for a in appts:
        if a.employee and a.employee not in emps:
            emps.append(a.employee)
    unassigned = [a for a in appts if not a.employee]
    blocks = BlockedSlot.query.filter(BlockedSlot.start_dt >= start_dt, BlockedSlot.start_dt < start_dt + timedelta(days=1), BlockedSlot.branch_id == bid).all()
    cols = [dict(e=e, appts=[a for a in appts if a.employee_id == e.id], blocks=[b for b in blocks if b.employee_id == e.id]) for e in emps]
    o, c = hhmm_to_min(br.open_time), hhmm_to_min(br.close_time)
    rk = Employee.query.filter_by(role="super_admin").first()
    rk_loc = rk.location_on(d) if rk else None
    ppm = 40.0 / (br.slot_minutes or 30)
    stats = dict(total=sum(1 for a in appts if a.is_active), done=sum(1 for a in appts if a.status == "completed"), cancelled=sum(1 for a in appts if a.status in ("cancelled", "no_show")),
                 value=sum(a.total for a in appts if a.is_active))
    return render_template("appointments/board.html", d=d, br=br, cols=cols, o=o, c=c, ppm=ppm, step=br.slot_minutes or 30, unassigned=unassigned, rk=rk, rk_loc=rk_loc,
                           stats=stats, prev=d - timedelta(days=1), nxt=d + timedelta(days=1), today=date.today(), page_title="Appointments",
                           now_min=(datetime.now().hour * 60 + datetime.now().minute) if d == date.today() else None, all_b=Branch.query.filter(Branch.id.in_(ids)).all())


@bp.route("/list")
@require_perm("appointments")
def list_():
    ids = scope_ids()
    d1 = parse_date(request.args.get("from"), date.today())
    d2 = parse_date(request.args.get("to"), date.today() + timedelta(days=7))
    q = Appointment.query.filter(Appointment.branch_id.in_(ids), Appointment.start_dt >= datetime.combine(d1, datetime.min.time()),
                                 Appointment.start_dt < datetime.combine(d2 + timedelta(days=1), datetime.min.time()))
    st, emp, src, s = request.args.get("status"), to_int(request.args.get("employee")), request.args.get("source"), (request.args.get("q") or "").strip()
    if st:
        q = q.filter(Appointment.status == st)
    if emp:
        q = q.filter(Appointment.employee_id == emp)
    if src == "online":
        q = q.filter(Appointment.source.in_(("online", "website", "instagram")))
    elif src == "offline":
        q = q.filter(Appointment.source.notin_(("online", "website", "instagram")))
    if s:
        q = q.join(Client).filter((Client.name.ilike(f"%{s}%")) | (Client.mobile.like(f"%{s}%")) | (Appointment.code.ilike(f"%{s}%")))
    rows = q.order_by(Appointment.start_dt).limit(500).all()
    emps = Employee.query.filter(Employee.takes_appointments == True, Employee.active == True).order_by(Employee.name).all()  # noqa: E712
    return render_template("appointments/list.html", rows=rows, d1=d1, d2=d2, emps=emps, statuses=APPT_STATUS, f=request.args, page_title="All appointments")


@bp.route("/new", methods=["GET", "POST"])
@require_perm("appointments")
def new():
    if request.method == "POST":
        try:
            bid = form_branch_id()
            guard_branch(bid)
            d = parse_date(request.form.get("date"))
            hhmm = request.form.get("time")
            sids = [int(x) for x in request.form.getlist("services")]
            mobile = clean_mobile(request.form.get("mobile"))
            name = (request.form.get("name") or "").strip()
            if not (bid and d and hhmm and sids):
                raise al.BookingError("Branch, service(s), date and time are required.")
            if len(mobile) != 10 or not name:
                raise al.BookingError("Enter the client's name and a valid 10-digit mobile number.")
            start = datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())
            client = al.find_or_create_client(name, mobile, request.form.get("source_label") or "Phone", bid, request.form.get("email"), request.form.get("gender"))
            a = al.create_appointment(bid, client, start, sids, to_int(request.form.get("employee_id")), source=request.form.get("source") or "offline",
                                      notes=request.form.get("notes"), created_by_id=current_user.id, status="confirmed")
            audit("create", "appointment", a.id, f"{a.code} {client.name}", bid)
            db.session.commit()
            wa.appointment_confirmed(a)
            db.session.commit()
            flash(f"Appointment <b>{a.code}</b> booked for {client.name}. <span class='badge'>WhatsApp confirmation sent (demo)</span>", "ok")
            return redirect(url_for("appt.board", date=d.isoformat(), b=bid))
        except (al.BookingError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "err")
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = {c.id: Service.query.filter_by(category_id=c.id, active=True).order_by(Service.name).all() for c in cats}
    pre = dict(date=request.values.get("date") or date.today().isoformat(), time=request.values.get("time"), employee=to_int(request.values.get("employee")),
               branch=to_int(request.values.get("branch")))
    return render_template("appointments/form.html", cats=cats, svcs=svcs, pre=pre, f=request.form, a=None, page_title="New appointment")


@bp.route("/<int:aid>")
@require_perm("appointments")
def view(aid):
    a = db.session.get(Appointment, aid) or abort(404)
    guard_branch(a.branch_id)
    history = Appointment.query.filter(Appointment.client_id == a.client_id, Appointment.id != a.id).order_by(Appointment.start_dt.desc()).limit(6).all()
    return render_template("appointments/view.html", a=a, history=history, page_title=f"Appointment {a.code}")


@bp.route("/<int:aid>/edit", methods=["GET", "POST"])
@require_perm("appointments")
def edit(aid):
    a = db.session.get(Appointment, aid) or abort(404)
    guard_branch(a.branch_id)
    if request.method == "POST":
        try:
            d = parse_date(request.form.get("date"))
            start = datetime.combine(d, datetime.strptime(request.form.get("time"), "%H:%M").time())
            sids = [int(x) for x in request.form.getlist("services")]
            note = al.reschedule(a, start, sids, to_int(request.form.get("employee_id")))
            a.notes = request.form.get("notes")
            audit("update", "appointment", a.id, note or "edited", a.branch_id)
            db.session.commit()
            if note:
                wa.appointment_updated(a, f"Change: {note}")
                db.session.commit()
                flash("Appointment updated. <span class='badge'>WhatsApp update sent to client (demo)</span>", "ok")
            else:
                flash("No changes made.", "ok")
            return redirect(url_for("appt.view", aid=a.id))
        except (al.BookingError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "err")
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = {c.id: Service.query.filter_by(category_id=c.id, active=True).order_by(Service.name).all() for c in cats}
    return render_template("appointments/form.html", cats=cats, svcs=svcs, pre={}, f=request.form, a=a, page_title=f"Modify {a.code}")


@bp.route("/<int:aid>/status", methods=["POST"])
@require_perm("appointments")
def set_status(aid):
    a = db.session.get(Appointment, aid) or abort(404)
    guard_branch(a.branch_id)
    st = request.form.get("status")
    if st not in dict(APPT_STATUS):
        return redirect(url_for("appt.view", aid=aid))
    old = a.status
    a.status = st
    if st in ("cancelled", "no_show"):
        a.cancel_reason = request.form.get("reason") or ("Did not arrive" if st == "no_show" else "Cancelled by salon")
    audit("status", "appointment", a.id, f"{old} -> {st}", a.branch_id)
    db.session.commit()
    if st == "cancelled":
        wa.appointment_cancelled(a)
        db.session.commit()
        flash("Appointment cancelled. <span class='badge'>WhatsApp cancellation sent to client (demo)</span>", "ok")
    else:
        flash(f"Status set to {dict(APPT_STATUS)[st]}.", "ok")
    if st == "completed" and current_user.has_perm("billing") and not a.invoice_id:
        return redirect(url_for("billing.new", appt=a.id))
    return redirect(request.form.get("next") or url_for("appt.view", aid=aid))


@bp.route("/block", methods=["POST"])
@require_perm("appointments")
def block():
    eid = to_int(request.form.get("employee_id"))
    e = db.session.get(Employee, eid)
    d = parse_date(request.form.get("date"))
    try:
        s = datetime.combine(d, datetime.strptime(request.form.get("from"), "%H:%M").time())
        t = datetime.combine(d, datetime.strptime(request.form.get("to"), "%H:%M").time())
        if t <= s:
            raise ValueError("End time must be after start time.")
        clash = Appointment.query.filter(Appointment.employee_id == eid, Appointment.status.in_(("booked", "confirmed", "arrived", "in_service")), Appointment.start_dt < t, Appointment.end_dt > s).first()
        if clash:
            raise ValueError(f"Cannot block – {clash.client.name} is booked at {clash.start_dt.strftime('%I:%M %p')}. Move or cancel that booking first.")
        bid = e.location_on(d) or e.home_branch_id
        guard_branch(bid)
        db.session.add(BlockedSlot(branch_id=bid, employee_id=eid, start_dt=s, end_dt=t, reason=request.form.get("reason") or "Blocked"))
        audit("block", "employee", eid, f"{d} {request.form.get('from')}-{request.form.get('to')}", bid)
        db.session.commit()
        flash("Time blocked. Clients can no longer book that window.", "ok")
    except ValueError as ex:
        flash(str(ex), "err")
    return redirect(request.referrer or url_for("appt.board"))


@bp.route("/block/<int:bid>/delete", methods=["POST"])
@require_perm("appointments")
def block_delete(bid):
    b = db.session.get(BlockedSlot, bid)
    if b:
        guard_branch(b.branch_id)
        db.session.delete(b)
        db.session.commit()
        flash("Block removed.", "ok")
    return redirect(request.referrer or url_for("appt.board"))


@bp.route("/location", methods=["POST"])
@manager_only
def location():
    """Choose which branch an employee (e.g. RK) works at on a particular day."""
    eid, d = to_int(request.form.get("employee_id")), parse_date(request.form.get("date"))
    bid = request.form.get("branch_id")
    e = db.session.get(Employee, eid)
    if not (e and d):
        return redirect(request.referrer or url_for("appt.board"))
    if not current_user.is_super and e.branch_id != current_user.branch_id:
        return redirect(url_for("appt.board"))
    day_start = datetime.combine(d, datetime.min.time())
    clash = Appointment.query.filter(Appointment.employee_id == eid, Appointment.status.in_(("booked", "confirmed", "arrived", "in_service")),
                                     Appointment.start_dt >= day_start, Appointment.start_dt < day_start + timedelta(days=1))
    ov = EmployeeDayBranch.query.filter_by(employee_id=eid, date=d).first()
    if bid == "default":
        if ov:
            db.session.delete(ov)
        target = e.home_branch_id
    else:
        target = to_int(bid) or None   # empty / 0 => not working that day
        if not ov:
            ov = EmployeeDayBranch(employee_id=eid, date=d)
            db.session.add(ov)
        ov.branch_id, ov.note = target, request.form.get("note")
    n_clash = clash.filter(Appointment.branch_id != (target or -1)).count()
    if n_clash:
        db.session.rollback()
        flash(f"{n_clash} booking(s) already exist for {e.name} at the current branch on that day. Reschedule or cancel them first, then change the branch.", "err")
    else:
        audit("location", "employee", eid, f"{d} -> branch {target}", target)
        db.session.commit()
        flash(f"{e.name} will work from <b>{db.session.get(Branch, target).name if target else 'no branch (off)'}</b> on {d.strftime('%a %d %b')}.", "ok")
    return redirect(request.referrer or url_for("appt.board", date=d.isoformat()))


# ---------------------------- services & pricing ----------------------------
@bp.route("/services", methods=["GET", "POST"])
@require_perm("appointments")
def services():
    if request.method == "POST":
        if not current_user.is_manager:
            return redirect(url_for("appt.services"))
        act = request.form.get("action")
        if act == "category":
            db.session.add(ServiceCategory(name=request.form["name"].strip(), sort=99))
        elif act in ("save", "add"):
            s = db.session.get(Service, to_int(request.form.get("id"))) if act == "save" else Service()
            s.name = request.form["name"].strip()
            s.category_id = to_int(request.form.get("category_id"))
            s.gender = request.form.get("gender") or "Unisex"
            s.price = to_float(request.form.get("price"))
            s.duration = to_int(request.form.get("duration"), 30)
            s.description = request.form.get("description")
            s.active = request.form.get("active") == "1" if act == "save" else True
            db.session.add(s)
        elif act == "recipe":
            sid = to_int(request.form.get("service_id"))
            it = to_int(request.form.get("item_id"))
            if sid and it:
                db.session.add(ServiceConsumable(service_id=sid, item_id=it, qty=to_float(request.form.get("qty"), 1)))
        elif act == "recipe_del":
            r = db.session.get(ServiceConsumable, to_int(request.form.get("id")))
            if r:
                db.session.delete(r)
        audit("edit", "service", None, act)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("appt.services", open=request.form.get("service_id") or ""))
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = Service.query.order_by(Service.category_id, Service.name).all()
    items = Item.query.filter_by(active=True, is_retail=False).order_by(Item.name).all()
    recipes = {}
    for r in ServiceConsumable.query.all():
        recipes.setdefault(r.service_id, []).append(r)
    return render_template("appointments/services.html", cats=cats, svcs=svcs, items=items, recipes=recipes, open_id=to_int(request.args.get("open")), page_title="Services & pricing")
