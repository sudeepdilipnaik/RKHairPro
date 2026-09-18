"""JSON endpoints used by the booking screens (public website and staff)."""
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

from ..extensions import db
from ..helpers import parse_date, to_int
from ..logic import appointments as al
from ..models import Client, Service

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/slots")
def slots():
    branch_id = to_int(request.args.get("branch"))
    d = parse_date(request.args.get("date"))
    ids = [i for i in (to_int(x) for x in request.args.get("services", "").split(",")) if i]
    emp = to_int(request.args.get("employee"))
    exclude = to_int(request.args.get("exclude"))
    public = (not current_user.is_authenticated) or request.args.get("public") == "1"
    if not branch_id or not d:
        return jsonify(ok=False, message="Choose a branch and date.", slots=[], employees=[])
    if d < date.today():
        return jsonify(ok=False, message="Please choose today or a future date.", slots=[], employees=[])
    svcs = Service.query.filter(Service.id.in_(ids)).all() if ids else []
    duration = sum(s.duration for s in svcs) or 30
    total = sum(s.price for s in svcs)
    slots_, emps = al.free_slots(branch_id, d, duration, emp, exclude_id=exclude, public=public)
    all_emps = al.bookable_employees(branch_id, d, public_only=public)
    msg = ""
    if not all_emps:
        msg = "No stylist is available at this branch on the chosen date. Please try another date or branch."
    elif not slots_:
        msg = "This day is fully booked for the selected services. Please pick another date."
    if al.is_holiday(branch_id, d):
        msg = "The salon is closed on this date (holiday)."
    return jsonify(ok=True, message=msg, duration=duration, total=total,
                   slots=[dict(time=s["time"], label=s["label"], employees=s["employees"]) for s in slots_],
                   employees=[dict(id=e.id, name=e.name, role=e.role_label, bio=e.bio or "") for e in all_emps])


@bp.route("/client")
def client_lookup():
    if not current_user.is_authenticated:
        return jsonify(found=False)
    mobile = "".join(c for c in request.args.get("mobile", "") if c.isdigit())[-10:]
    c = Client.query.filter_by(mobile=mobile).first() if len(mobile) == 10 else None
    if not c:
        return jsonify(found=False)
    return jsonify(found=True, id=c.id, name=c.name, email=c.email or "", points=c.loyalty_points or 0, gender=c.gender or "")
