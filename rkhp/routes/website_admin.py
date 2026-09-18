"""Website Studio - lets the Super Admin (or anyone granted the 'website' permission) edit the public site."""
import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..helpers import DEFAULT_SETTINGS, audit, require_perm, set_setting, setting, to_int
from ..logic.web import IMAGE_EXT, TAGS, VIDEO_EXT, delete_media, save_media, web_dir
from ..models import Setting, Testimonial

bp = Blueprint("web", __name__, url_prefix="/website")

TEXT_KEYS = [k for k in DEFAULT_SETTINGS if k.startswith("site_") and not k.startswith("site_show_") and k != "site_meet_photo"]
TOGGLE_KEYS = [k for k in DEFAULT_SETTINGS if k.startswith("site_show_")]
SECTIONS = [("meet", "Meet RK"), ("videos", "Transformation videos"), ("services", "Services & pricing"), ("reviews", "Client testimonials"),
            ("team", "Stylists"), ("branches", "Branches"), ("cta", "Closing call-to-action & follow band")]


def _back(tab):
    return redirect(url_for("web.index", tab=tab))


def hero_info():
    hv = setting("hero_video")
    if hv == "default":
        path = os.path.join(current_app.static_folder, "media", "rk_logo_animation.mp4")
        src, label = url_for("static", filename="media/rk_logo_animation.mp4"), "Original RK Hair Pro logo animation"
    elif hv:
        path = os.path.join(web_dir(), os.path.basename(hv))
        src, label = url_for("public.media", name=hv), "Uploaded animation"
    else:
        return dict(mode="none", src=None, label="Static logo (no animation)", mb=0)
    mb = round(os.path.getsize(path) / 1048576, 1) if os.path.exists(path) else 0
    return dict(mode="default" if hv == "default" else "custom", src=src, label=label, mb=mb)


@bp.route("/")
@require_perm("website")
def index():
    tab = request.args.get("tab", "content")
    rows = Testimonial.query.order_by(Testimonial.kind.desc(), Testimonial.sort, Testimonial.id).all()
    vals = {k: setting(k) for k in TEXT_KEYS + TOGGLE_KEYS + ["site_meet_photo", "hero_loop", "hero_sound_btn"]}
    return render_template("website/studio.html", tab=tab, v=vals, sections=SECTIONS, hero=hero_info(), tags=TAGS, page_title="Website Studio",
                           videos=[r for r in rows if r.kind == "video"], quotes=[r for r in rows if r.kind == "text"], n_sample=sum(1 for r in rows if r.is_sample))


# ----------------------------------------------------------------------------
# page text, section visibility, RK photo
# ----------------------------------------------------------------------------
@bp.route("/content", methods=["POST"])
@require_perm("website")
def content():
    f = request.form
    if f.get("action") == "reset":
        delete_media(setting("site_meet_photo"))
        Setting.query.filter(Setting.key.like("site\\_%", escape="\\")).delete(synchronize_session=False)
        flash("All page text, section settings and the photo are back to the defaults.", "ok")
    else:
        for k in TEXT_KEYS:
            if k in f:
                set_setting(k, f.get(k, "").strip())
        for k in TOGGLE_KEYS:
            set_setting(k, "1" if f.get(k) == "1" else "0")
        photo = save_media(request.files.get("meet_photo"), IMAGE_EXT)
        if photo:
            delete_media(setting("site_meet_photo"))
            set_setting("site_meet_photo", photo)
        elif f.get("remove_photo") == "1":
            delete_media(setting("site_meet_photo"))
            set_setting("site_meet_photo", "")
        flash("Saved – the website is updated instantly.", "ok")
    audit("website", "content", None, f.get("action", "save"))
    db.session.commit()
    return _back("content")


# ----------------------------------------------------------------------------
# hero logo animation
# ----------------------------------------------------------------------------
@bp.route("/animation", methods=["POST"])
@require_perm("website")
def animation():
    f = request.form
    act = f.get("action")
    hv = setting("hero_video")
    if act == "upload":
        up = save_media(request.files.get("video_file"), VIDEO_EXT)
        if up:
            if hv not in ("", "default"):
                delete_media(hv)
            set_setting("hero_video", up)
            flash("New logo animation is live on the home page.", "ok")
        else:
            flash("Please choose a .mp4, .webm or .mov file.", "err")
    elif act == "remove":
        if hv not in ("", "default"):
            delete_media(hv)
        set_setting("hero_video", "")
        flash("Animation removed – the home page now shows the static logo.", "ok")
    elif act == "restore":
        if hv not in ("", "default"):
            delete_media(hv)
        set_setting("hero_video", "default")
        flash("The original RK Hair Pro logo animation is back.", "ok")
    elif act == "options":
        set_setting("hero_loop", "1" if f.get("hero_loop") == "1" else "0")
        set_setting("hero_sound_btn", "1" if f.get("hero_sound_btn") == "1" else "0")
        flash("Animation options saved.", "ok")
    audit("website", "animation", None, act or "")
    db.session.commit()
    return _back("animation")


# ----------------------------------------------------------------------------
# videos & testimonials (sliders)
# ----------------------------------------------------------------------------
@bp.route("/items", methods=["POST"])
@require_perm("website")
def items():
    f = request.form
    act = f.get("action", "save")
    tab = "reviews" if f.get("kind") == "text" or f.get("tab") == "reviews" else "videos"
    t = db.session.get(Testimonial, to_int(f.get("id"))) if f.get("id") else None
    if act == "delete" and t:
        delete_media(t.video_file); delete_media(t.poster_file)
        db.session.delete(t)
        flash("Removed from the website.", "ok")
    elif act == "toggle" and t:
        t.active = not t.active
        flash("Now shown on the website." if t.active else "Hidden from the website.", "ok")
    elif act == "clear_samples":
        n = 0
        for s in Testimonial.query.filter_by(is_sample=True).all():
            delete_media(s.video_file); db.session.delete(s); n += 1
        flash(f"Removed {n} sample item(s). Only your own content remains.", "ok")
    else:
        kind = f.get("kind", "video")
        if kind == "text" and not (f.get("quote") or "").strip():
            flash("Please enter the testimonial text.", "err")
            return _back("reviews")
        if not t:
            t = Testimonial(kind=kind)
            db.session.add(t)
        t.kind = kind
        t.tag, t.title = f.get("tag") or "Other", (f.get("title") or "").strip()
        t.client_name, t.quote = (f.get("client_name") or "").strip(), (f.get("quote") or "").strip()[:500]
        t.rating = max(1, min(5, to_int(f.get("rating"), 5)))
        t.sort = to_int(f.get("sort"), 100)
        t.active = f.get("active", "1") == "1"
        t.is_sample = False
        if kind == "video":
            up = save_media(request.files.get("video_file"), VIDEO_EXT)
            if up:
                delete_media(t.video_file)
                t.video_file = up
            elif request.files.get("video_file") and request.files["video_file"].filename:
                flash("That video format isn't supported. Please upload .mp4, .webm or .mov.", "err")
            pf = save_media(request.files.get("poster_file"), IMAGE_EXT)
            if pf:
                delete_media(t.poster_file)
                t.poster_file = pf
            if f.get("video_url") is not None:
                t.video_url = f.get("video_url").strip() or None
        flash("Saved – the website is updated instantly.", "ok")
    audit("website", "testimonial", t.id if t else None, act)
    db.session.commit()
    return _back(tab)
