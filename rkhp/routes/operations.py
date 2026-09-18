from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..helpers import all_branches, audit, form_branch_id, guard_branch, manager_only, notify, parse_date, require_perm, scope_ids, super_only, to_float, to_int
from ..models import AuditLog, Branch, ChecklistEntry, ChecklistItem, Employee, Equipment, Holiday, Task

bp = Blueprint("ops", __name__, url_prefix="/operations")


@bp.route("/")
@require_perm("operations")
def index():
    tab = request.args.get("tab", "checklists")
    ids = scope_ids()
    ctx = dict(tab=tab, page_title="Operations")
    if tab == "checklists":
        bid = to_int(request.args.get("b")) or ids[0]
        if bid not in ids:
            abort(403)
        d = parse_date(request.args.get("date"), date.today())
        items = ChecklistItem.query.filter(ChecklistItem.active == True, (ChecklistItem.branch_id == bid) | (ChecklistItem.branch_id.is_(None))).order_by(ChecklistItem.shift, ChecklistItem.id).all()  # noqa: E712
        done = {e.item_id: e for e in ChecklistEntry.query.filter_by(branch_id=bid, date=d)}
        groups = {}
        for it in items:
            groups.setdefault(it.shift, []).append(it)
        ctx.update(bid=bid, d=d, groups=groups, done=done, br=db.session.get(Branch, bid), all_b=Branch.query.filter(Branch.id.in_(ids)).all(), n_items=len(items), n_done=len(done))
    elif tab == "tasks":
        st = request.args.get("status")
        q = Task.query.filter(Task.branch_id.in_(ids) | Task.branch_id.is_(None))
        if st:
            q = q.filter(Task.status == st)
        rows = q.order_by(Task.status == "done", Task.due_date).all()
        emps = Employee.query.filter(Employee.active == True, Employee.branch_id.in_(ids)).order_by(Employee.name).all()  # noqa: E712
        ctx.update(rows=rows, emps=emps, st=st, today=date.today())
    elif tab == "equipment":
        rows = Equipment.query.filter(Equipment.branch_id.in_(ids)).order_by(Equipment.next_service).all()
        ctx.update(rows=rows, today=date.today())
    elif tab == "holidays":
        ctx.update(rows=Holiday.query.order_by(Holiday.date).all(), today=date.today())
    return render_template("ops/index.html", **ctx)


@bp.route("/check", methods=["POST"])
@require_perm("operations")
def check():
    iid, bid, d = to_int(request.form.get("item")), to_int(request.form.get("branch")), parse_date(request.form.get("date"))
    guard_branch(bid)
    e = ChecklistEntry.query.filter_by(item_id=iid, branch_id=bid, date=d).first()
    if e:
        db.session.delete(e)
    else:
        db.session.add(ChecklistEntry(item_id=iid, branch_id=bid, date=d, done_by_id=current_user.id))
    db.session.commit()
    return redirect(request.referrer or url_for("ops.index"))


@bp.route("/checklist-item", methods=["POST"])
@manager_only
def checklist_item():
    if request.form.get("delete"):
        it = db.session.get(ChecklistItem, to_int(request.form.get("delete")))
        if it:
            it.active = False
    else:
        bid = current_user.branch_id or to_int(request.form.get("branch_id")) or None
        db.session.add(ChecklistItem(title=request.form["title"].strip(), shift=request.form.get("shift", "opening"), branch_id=bid))
    db.session.commit()
    return redirect(request.referrer or url_for("ops.index"))


@bp.route("/task", methods=["POST"])
@require_perm("operations")
def task():
    f = request.form
    if f.get("id"):
        t = db.session.get(Task, to_int(f.get("id"))) or abort(404)
        t.status = f.get("status", t.status)
        t.completed_at = datetime.now() if t.status == "done" else None
    else:
        t = Task(title=f["title"].strip(), detail=f.get("detail"), branch_id=current_user.branch_id or to_int(f.get("branch_id")) or None, assigned_to_id=to_int(f.get("assigned_to_id")),
                 due_date=parse_date(f.get("due_date")), priority=f.get("priority", "normal"), created_by_id=current_user.id)
        db.session.add(t)
        if t.assigned_to_id:
            notify(t.assigned_to_id, "New task assigned", t.title, "/me/", "task")
    db.session.commit()
    flash("Task saved.", "ok")
    return redirect(url_for("ops.index", tab="tasks"))


@bp.route("/equipment", methods=["POST"])
@require_perm("operations")
def equipment():
    f = request.form
    if f.get("service_id"):
        e = db.session.get(Equipment, to_int(f.get("service_id"))) or abort(404)
        guard_branch(e.branch_id)
        e.last_service = date.today()
        e.next_service = date.today() + timedelta(days=to_int(f.get("days"), 90))
        e.status = "working"
    else:
        e = db.session.get(Equipment, to_int(f.get("id"))) if f.get("id") else Equipment(branch_id=form_branch_id())
        guard_branch(e.branch_id)
        e.name, e.cost = f["name"].strip(), to_float(f.get("cost"))
        e.purchase_date, e.warranty_till, e.next_service, e.status, e.notes = parse_date(f.get("purchase_date")), parse_date(f.get("warranty_till")), parse_date(f.get("next_service")), f.get("status", "working"), f.get("notes")
        if not e.id:
            db.session.add(e)
    db.session.commit()
    flash("Equipment updated.", "ok")
    return redirect(url_for("ops.index", tab="equipment"))


@bp.route("/holiday", methods=["POST"])
@manager_only
def holiday():
    if request.form.get("delete"):
        h = db.session.get(Holiday, to_int(request.form.get("delete")))
        if h:
            db.session.delete(h)
    else:
        db.session.add(Holiday(date=parse_date(request.form.get("date")), name=request.form.get("name"), branch_id=current_user.branch_id or to_int(request.form.get("branch_id")) or None))
    db.session.commit()
    flash("Holiday calendar updated. The salon will not accept bookings on holidays.", "ok")
    return redirect(url_for("ops.index", tab="holidays"))


@bp.route("/audit")
@super_only
def audit_log():
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(300).all()
    return render_template("ops/audit.html", rows=rows, page_title="Audit trail")
