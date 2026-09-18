"""Public-website content: media storage and sample content."""
import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Testimonial

VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
TAGS = ["Haircut", "Haircolour", "Styling", "Grooming", "Treatment", "Bridal", "Skin", "Other"]


def web_dir():
    d = os.path.join(current_app.config["UPLOAD_FOLDER"], "web")
    os.makedirs(d, exist_ok=True)
    return d


def save_media(file_storage, allowed):
    """Save an uploaded file into uploads/web and return its stored name (or None)."""
    if not file_storage or not file_storage.filename:
        return None
    ext = os.path.splitext(secure_filename(file_storage.filename))[1].lower()
    if ext not in allowed:
        return None
    name = f"{uuid.uuid4().hex[:12]}{ext}"
    file_storage.save(os.path.join(web_dir(), name))
    return name


def delete_media(name):
    if name:
        try:
            os.remove(os.path.join(web_dir(), os.path.basename(name)))
        except OSError:
            pass


def ensure_website_content():
    """Sample sliders so the site looks complete on day one. Everything is editable / removable in the app."""
    if Testimonial.query.count():
        return
    videos = [("Haircut", "Signature Cut by RK", "Aarav M.", "Precision fade with a textured top."),
              ("Haircolour", "Rich Espresso Global Colour", "Ananya S.", "Deep, glossy colour that lasts."),
              ("Haircolour", "Balayage Transformation", "Meera K.", "Soft caramel highlights, zero brassiness."),
              ("Grooming", "Beard & Grooming Refresh", "Kabir J.", "Sharp lines, clean finish."),
              ("Treatment", "Keratin Smoothening", "Isha P.", "Frizz-free and shiny for months.")]
    for i, (tag, title, who, cap) in enumerate(videos):
        db.session.add(Testimonial(kind="video", tag=tag, title=title, client_name=who, quote=cap, sort=10 + i, is_sample=True))
    quotes = [("Riya D.", "Global Colour", "I walked in with a Pinterest photo and walked out with exactly that. RK listens, and the finish is flawless.", 5),
              ("Siddharth M.", "Signature Cut", "Appointment-only means zero waiting. The cut was sharp and the whole experience felt premium.", 5),
              ("Priya N.", "Keratin", "My hair has never been this smooth. The team explained every step and the price was fair.", 5),
              ("Karan T.", "Beard & Haircut", "Best grooming in South Mumbai. I book the same slot every month.", 5),
              ("Neha B.", "Party Makeup", "Booked online at midnight, got a WhatsApp confirmation instantly and a reminder before the visit. Super smooth.", 5),
              ("Aditya R.", "Hair Spa", "Spotless salon, warm staff, and my hair feels new. Highly recommended.", 4)]
    for i, (who, svc, q, r) in enumerate(quotes):
        db.session.add(Testimonial(kind="text", tag=svc, title=svc, client_name=who, quote=q, rating=r, sort=10 + i, is_sample=True))
    db.session.commit()
