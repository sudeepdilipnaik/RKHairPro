"""Public website: landing page, online booking, feedback form."""
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from sqlalchemy import func

from ..extensions import db
from ..helpers import all_branches, clean_mobile, parse_date, setting, setting_num, to_int
from ..logic import appointments as al
from ..logic import whatsapp as wa
from ..logic.web import web_dir
from ..models import Appointment, Branch, Employee, Feedback, Service, ServiceCategory, Testimonial

bp = Blueprint("public", __name__)

SRC_LABEL = {"instagram": "Instagram", "website": "Website", "google": "Google", "whatsapp": "WhatsApp", "facebook": "Facebook", "qr": "Salon QR"}


@bp.route("/")
def home():
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = {c.id: Service.query.filter_by(category_id=c.id, active=True).order_by(Service.price).all() for c in cats}
    cats = [c for c in cats if svcs[c.id]]
    team = Employee.query.filter_by(active=True, takes_appointments=True, public_visible=True).order_by(Employee.role != "super_admin", Employee.name).all()
    rk = Employee.query.filter_by(role="super_admin").first()
    avg, n_rev = db.session.query(func.avg(Feedback.rating), func.count(Feedback.rating)).filter(Feedback.rating.isnot(None)).first()
    q = Testimonial.query.filter_by(active=True).order_by(Testimonial.sort, Testimonial.id)
    videos = [t for t in q if t.kind == "video"]
    quotes = [t for t in q if t.kind == "text"]
    hv = setting("hero_video")
    hero_src = (url_for("public.site_media", name="rk_logo_animation_web.mp4") if hv == "default"
                else (url_for("public.media", name=hv) if hv else None))
    meet_photo = url_for("public.media", name=setting("site_meet_photo")) if setting("site_meet_photo") else url_for("static", filename="img/owner_small.jpg")
    show = lambda k: setting("site_show_" + k, "1") == "1"      # noqa: E731
    return render_template("public/home.html", cats=cats, svcs=svcs, team=team, rk=rk, branches=all_branches(), videos=videos, quotes=quotes,
                           avg=round(avg, 1) if avg else None, n_rev=n_rev or 0, hero_src=hero_src, hero_loop=setting("hero_loop") == "1",
                           hero_sound=setting("hero_sound_btn") == "1", meet_photo=meet_photo, show=show,
                           meet_points=[p.strip() for p in setting("site_meet_points", "").splitlines() if p.strip()], page_title="Premium Unisex Salon")


@bp.route("/site-media/<path:name>")
def site_media(name):
    """Bundled site media (the logo animation). Served by the app - not the host's static mapping - so phones get
    byte-range support, which iPhones require and which lets playback start before the file has fully downloaded."""
    return send_from_directory(os.path.join(current_app.static_folder, "media"), os.path.basename(name), conditional=True, max_age=86400)


@bp.route("/media/<path:name>")
def media(name):
    """Public website media (videos / posters only - never the private uploads such as KYC documents)."""
    return send_from_directory(web_dir(), os.path.basename(name), conditional=True)


@bp.route("/book", methods=["GET", "POST"])
def book():
    src = (request.values.get("src") or "website").lower()
    branches = all_branches()
    cats = ServiceCategory.query.order_by(ServiceCategory.sort).all()
    svcs = {c.id: Service.query.filter_by(category_id=c.id, active=True).order_by(Service.name).all() for c in cats}
    max_days = int(setting_num("booking_advance_days", 120))
    if request.method == "POST":
        try:
            branch_id = to_int(request.form.get("branch_id"))
            d = parse_date(request.form.get("date"))
            hhmm = request.form.get("time")
            sids = [int(x) for x in request.form.getlist("services")]
            name = (request.form.get("name") or "").strip()
            mobile = clean_mobile(request.form.get("mobile"))
            if not (branch_id and d and hhmm and sids):
                raise al.BookingError("Please choose a branch, service(s), date and time.")
            if len(mobile) != 10 or not name:
                raise al.BookingError("Please enter your name and a valid 10-digit mobile number.")
            start = datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())
            client = al.find_or_create_client(name, mobile, SRC_LABEL.get(src, "Website"), branch_id, request.form.get("email"))
            a = al.create_appointment(branch_id, client, start, sids, to_int(request.form.get("employee_id")),
                                      source=src if src in ("instagram", "website") else "online", notes=request.form.get("notes"), public=True, status="confirmed")
            db.session.commit()
            wa.appointment_confirmed(a)
            db.session.commit()
            return redirect(url_for("public.book_done", code=a.code))
        except (al.BookingError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "err")
    default_date = date.today() if datetime.now().hour < 18 else date.today() + timedelta(days=1)
    return render_template("public/book.html", branches=branches, cats=cats, svcs=svcs, src=src, max_days=max_days, today=date.today(), default_date=default_date,
                           f=request.form, page_title="Book an appointment")


@bp.route("/book/done/<code>")
def book_done(code):
    a = Appointment.query.filter_by(code=code).first_or_404()
    return render_template("public/done.html", a=a, page_title="Booking confirmed")


@bp.route("/my-booking", methods=["GET", "POST"])
def lookup():
    a = None
    if request.method == "POST":
        a = Appointment.query.filter_by(code=(request.form.get("code") or "").strip().upper()).first()
        if not a or a.client.mobile != clean_mobile(request.form.get("mobile")):
            a = None
            flash("We couldn't find a booking with those details.", "err")
    return render_template("public/lookup.html", a=a, page_title="Check my booking")


@bp.route("/feedback/<tok>", methods=["GET", "POST"])
def feedback_form(tok):
    fb = Feedback.query.filter_by(token=tok).first_or_404()
    if request.method == "POST":
        fb.rating = max(1, min(5, to_int(request.form.get("rating"), 5)))
        fb.comment = (request.form.get("comment") or "")[:400]
        fb.status, fb.submitted_at = "received", datetime.now()
        db.session.commit()
        return render_template("public/feedback.html", fb=fb, done=True, review=setting("google_review_link"), page_title="Thank you")
    return render_template("public/feedback.html", fb=fb, done=fb.rating is not None, review=setting("google_review_link"), page_title="Rate your visit")
