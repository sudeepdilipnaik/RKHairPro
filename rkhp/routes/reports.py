import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, render_template, request
from flask_login import current_user

from ..helpers import get_period, month_bounds, require_perm, scope_ids
from ..logic import reports as rl

bp = Blueprint("rep", __name__, url_prefix="/reports")


@bp.route("/")
@require_perm("reports")
def index():
    groups = []
    for k, label in rl.DEPTS:
        items = [m for m in rl.REPORTS.values() if m["dept"] == k]
        groups.append((k, label, items))
    return render_template("reports/index.html", groups=groups, active=request.args.get("dept", "finance"), page_title="Reports")


def _run(key):
    meta = rl.REPORTS.get(key) or abort(404)
    pk, d1, d2, label = get_period("mtd")
    ym = request.args.get("month") or date.today().strftime("%Y-%m")
    if meta["month"]:
        d1, d2 = month_bounds(ym)
        label = d1.strftime("%B %Y")
    R = rl.make_ctx(scope_ids(), d1, d2, request.args)
    R.month = ym
    m, out = rl.run(key, R)
    return m, out, pk, d1, d2, label, ym


@bp.route("/<key>")
@require_perm("reports")
def view(key):
    m, out, pk, d1, d2, label, ym = _run(key)
    same = [x for x in rl.REPORTS.values() if x["dept"] == m["dept"]]
    return render_template("reports/view.html", m=m, out=out, key=pk, d1=d1, d2=d2, label=label, ym=ym, same=same, dept_label=dict(rl.DEPTS)[m["dept"]], page_title=m["title"])


@bp.route("/<key>.csv")
@require_perm("reports")
def csv_(key):
    m, out, pk, d1, d2, label, ym = _run(key)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"RK Hair Pro – {m['title']}", label])
    w.writerow([c["t"] for c in out["columns"]])
    for r in out["rows"]:
        w.writerow([x.isoformat() if hasattr(x, "isoformat") else x for x in r])
    if out["totals"]:
        w.writerow(out["totals"])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={key}_{d1}_{d2}.csv"})
