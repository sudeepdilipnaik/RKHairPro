from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..helpers import all_branches, audit, manager_only, parse_date, require_perm, scope_ids, setting, to_float, to_int
from ..logic import whatsapp as wa
from ..models import Branch, Campaign, Client, Coupon, Feedback, Invoice, Lead

bp = Blueprint("mkt", __name__, url_prefix="/marketing")
SEGMENTS = [("all", "All clients"), ("new", "New clients (last 30 days)"), ("vip", "VIP (spent ₹25,000+)"), ("lapsed", "Lapsed (no visit in 60+ days)"),
            ("birthday", "Birthdays this month"), ("female", "Women"), ("male", "Men"), ("never", "Never billed yet")]
LEAD_SOURCES = ["Instagram", "Website", "Google", "Walk-in", "Phone", "Referral", "WhatsApp", "Other"]


def segment_clients(seg, branch_ids):
    today = date.today()
    st = {cid: (n, s, last) for cid, n, s, last in db.session.query(Invoice.client_id, func.count(Invoice.id), func.sum(Invoice.subtotal - Invoice.discount), func.max(Invoice.date))
          .filter(Invoice.branch_id.in_(branch_ids), Invoice.status != "void", Invoice.client_id.isnot(None)).group_by(Invoice.client_id)}
    out = []
    for c in Client.query.filter(Client.whatsapp_optin == True).all():  # noqa: E712
        if c.branch_id and c.branch_id not in branch_ids and c.id not in st:
            continue
        n, s, last = st.get(c.id, (0, 0, None))
        ok = (seg == "all" or (seg == "new" and (today - c.created_at.date()).days <= 30) or (seg == "vip" and (s or 0) >= 25000) or (seg == "lapsed" and last and (today - last).days > 60)
              or (seg == "birthday" and c.dob and c.dob.month == today.month) or (seg == "female" and c.gender == "Female") or (seg == "male" and c.gender == "Male") or (seg == "never" and n == 0))
        if ok:
            out.append(c)
    return out


@bp.route("/")
@require_perm("marketing")
def index():
    ids = scope_ids()
    today = date.today()
    m1 = today.replace(day=1)
    new_clients = Client.query.filter(Client.created_at >= datetime.combine(m1, datetime.min.time())).count()
    leads_open = Lead.query.filter(Lead.status.in_(("new", "contacted"))).count()
    fb = db.session.query(func.avg(Feedback.rating), func.count(Feedback.rating)).filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(ids)).first()
    bdays = [c for c in Client.query.filter(Client.dob.isnot(None)).all() if c.dob.month == today.month]
    bdays.sort(key=lambda c: c.dob.day)
    upcoming = [c for c in bdays if c.dob.day >= today.day][:8]
    lapsed = len(segment_clients("lapsed", ids))
    camps = Campaign.query.order_by(Campaign.created_at.desc()).limit(4).all()
    funnel = {s: Lead.query.filter_by(status=s).count() for s in ("new", "contacted", "booked", "lost")}
    src = db.session.query(func.coalesce(Client.source, "Unknown"), func.count(Client.id)).group_by(Client.source).order_by(func.count(Client.id).desc()).limit(6).all()
    low_fb = Feedback.query.filter(Feedback.rating <= 2, Feedback.status == "received", Feedback.branch_id.in_(ids)).order_by(Feedback.submitted_at.desc()).limit(4).all()
    return render_template("marketing/index.html", new_clients=new_clients, leads_open=leads_open, avg=fb[0] or 0, n_fb=fb[1], bdays=bdays, upcoming=upcoming, lapsed=lapsed, camps=camps,
                           funnel=funnel, src=src, low_fb=low_fb, coupons=Coupon.query.filter_by(active=True).count(), page_title="Marketing")


@bp.route("/campaigns", methods=["GET", "POST"])
@require_perm("marketing")
def campaigns():
    ids = scope_ids()
    if request.method == "POST":
        f = request.form
        seg = f.get("segment", "all")
        targets = segment_clients(seg, ids)
        body = (f.get("message") or "").strip()
        if not body:
            flash("Write the message first.", "err")
        elif f.get("action") == "preview":
            flash(f"This audience has {len(targets)} client(s).", "ok")
        else:
            c = Campaign(name=f["name"].strip(), segment=seg, message=body, branch_id=current_user.branch_id or to_int(f.get("branch_id")) or None, created_by_id=current_user.id,
                         status="sent", sent_at=datetime.now(), sent_count=len(targets))
            db.session.add(c)
            for cl in targets:
                wa.queue("campaign", cl.mobile, cl.name, body.replace("{name}", cl.name.split()[0]).replace("{book_link}", wa.book_link()), c.branch_id)
            audit("send", "campaign", None, f"{c.name} -> {len(targets)}")
            db.session.commit()
            flash(f"Campaign <b>{c.name}</b> sent to {len(targets)} client(s) <span class='badge'>WhatsApp – demo, see Outbox</span>", "wa")
        return redirect(url_for("mkt.campaigns"))
    rows = Campaign.query.order_by(Campaign.created_at.desc()).limit(50).all()
    counts = {k: len(segment_clients(k, ids)) for k, _ in SEGMENTS}
    return render_template("marketing/campaigns.html", rows=rows, segments=SEGMENTS, counts=counts, page_title="Campaigns & broadcasts", pre=request.args)


@bp.route("/birthdays/send", methods=["POST"])
@require_perm("marketing")
def birthdays_send():
    n = 0
    for c in segment_clients("birthday", scope_ids()):
        wa.simple("birthday", c, client=c.name.split()[0], offer="15% off (code BDAYSPL)", book_link=wa.book_link())
        n += 1
    db.session.commit()
    flash(f"Birthday greetings queued for {n} client(s) <span class='badge'>WhatsApp – demo</span>", "wa")
    return redirect(request.referrer or url_for("mkt.index"))


@bp.route("/coupons", methods=["GET", "POST"])
@require_perm("marketing")
def coupons():
    if request.method == "POST":
        if not current_user.is_manager:
            abort(403)
        f = request.form
        c = db.session.get(Coupon, to_int(f.get("id"))) if f.get("id") else Coupon()
        if not f.get("id") and Coupon.query.filter(func.upper(Coupon.code) == f["code"].strip().upper()).first():
            flash("That coupon code already exists.", "err")
            return redirect(url_for("mkt.coupons"))
        c.code, c.description, c.kind, c.value = f["code"].strip().upper(), f.get("description"), f.get("kind", "percent"), to_float(f.get("value"))
        c.min_bill, c.max_discount, c.usage_limit = to_float(f.get("min_bill")), to_float(f.get("max_discount")), to_int(f.get("usage_limit"), 0)
        c.valid_from, c.valid_to = parse_date(f.get("valid_from")), parse_date(f.get("valid_to"))
        c.active = f.get("active", "1") == "1"
        if not f.get("id"):
            db.session.add(c)
        db.session.commit()
        flash("Coupon saved.", "ok")
        return redirect(url_for("mkt.coupons"))
    return render_template("marketing/coupons.html", rows=Coupon.query.order_by(Coupon.active.desc(), Coupon.code).all(), today=date.today(), page_title="Offers & coupons")


@bp.route("/leads", methods=["GET", "POST"])
@require_perm("marketing")
def leads():
    if request.method == "POST":
        f = request.form
        if f.get("id"):
            l = db.session.get(Lead, to_int(f.get("id"))) or abort(404)
            l.status = f.get("status", l.status)
            l.notes = f.get("notes", l.notes)
            l.follow_up = parse_date(f.get("follow_up")) or l.follow_up
        else:
            db.session.add(Lead(name=f["name"].strip(), mobile=f.get("mobile"), source=f.get("source"), interest=f.get("interest"), branch_id=current_user.branch_id or to_int(f.get("branch_id")) or None,
                                follow_up=parse_date(f.get("follow_up")), notes=f.get("notes")))
        db.session.commit()
        flash("Lead saved.", "ok")
        return redirect(url_for("mkt.leads"))
    st = request.args.get("status")
    q = Lead.query
    if not current_user.is_super:
        q = q.filter((Lead.branch_id == current_user.branch_id) | (Lead.branch_id.is_(None)))
    if st:
        q = q.filter(Lead.status == st)
    return render_template("marketing/leads.html", rows=q.order_by(Lead.status.in_(("booked", "lost")), Lead.follow_up).all(), sources=LEAD_SOURCES, st=st, today=date.today(), page_title="Lead & enquiry tracker")


@bp.route("/links")
@require_perm("marketing")
def links():
    base = request.host_url.rstrip("/")
    rows = [("Instagram bio", "instagram"), ("Website button", "website"), ("Google Business profile", "google"), ("WhatsApp status / catalogue", "whatsapp"), ("Salon QR poster", "qr")]
    return render_template("marketing/links.html", base=base, rows=rows, page_title="Booking links")


@bp.route("/feedback", methods=["GET", "POST"])
@require_perm("marketing")
def feedback():
    ids = scope_ids()
    if request.method == "POST":
        f = db.session.get(Feedback, to_int(request.form.get("id"))) or abort(404)
        f.status, f.follow_up_note = "resolved", request.form.get("note")
        db.session.commit()
        flash("Marked as followed up.", "ok")
        return redirect(url_for("mkt.feedback", **({"low": 1} if request.args.get("low") else {})))
    q = Feedback.query.filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(ids))
    if request.args.get("low"):
        q = q.filter(Feedback.rating <= 3)
    rows = q.order_by(Feedback.submitted_at.desc()).limit(120).all()
    by_branch = db.session.query(Branch.name, func.avg(Feedback.rating), func.count(Feedback.rating)).join(Feedback, Feedback.branch_id == Branch.id).filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(ids)).group_by(Branch.id).all()
    from ..models import Employee
    by_emp = db.session.query(Employee.name, func.avg(Feedback.rating), func.count(Feedback.rating)).join(Feedback, Feedback.employee_id == Employee.id).filter(Feedback.rating.isnot(None), Feedback.branch_id.in_(ids)).group_by(Employee.id).order_by(func.avg(Feedback.rating).desc()).all()
    return render_template("marketing/feedback.html", rows=rows, by_branch=by_branch, by_emp=by_emp, low=request.args.get("low"), page_title="Feedback & reviews")
