"""Realistic demo data for RK Hair Pro.  All names, numbers and prices are SAMPLE data."""
import random
from datetime import date, datetime, timedelta

from flask import current_app

from .extensions import db
from .helpers import DEFAULT_SETTINGS, fy_start, month_bounds, set_setting
from .logic import appointments as appt_logic
from .logic import billing, finance as fin, inventory as inv_logic, payroll as pr
from .models import *  # noqa: F401,F403

RND = random.Random(2026)
TODAY = date.today


def wipe():
    db.session.remove()
    db.drop_all()
    db.create_all()
    fin.clear_cache()


def dt(d, hhmm):
    h, m = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(h), int(m))


def seed(history=True, mode="demo"):
    """Create all demo data.  `history=False` seeds only masters (fast); mode='clean' seeds an empty business."""
    RND.seed(2026)
    fin.clear_cache()
    pw = current_app.config["DEFAULT_PASSWORD"]
    for k, v in DEFAULT_SETTINGS.items():
        set_setting(k, v)
    today = date.today()
    fy = fy_start(today)

    # ---------------- branches ----------------
    tdw = Branch(name="Thakurdwar", code="TDW", address="Thakurdwar, Girgaon, South Mumbai", phone="9820011001", monthly_target=650000)
    skn = Branch(name="Sikka Nagar", code="SKN", address="Sikka Nagar, Girgaon, South Mumbai", phone="9820011002", monthly_target=500000)
    db.session.add_all([tdw, skn])
    db.session.flush()

    # ---------------- heads ----------------
    heads = [
        (fin.H_SERVICE, "income", "revenue", "Salon income", True), (fin.H_PRODUCT, "income", "revenue", "Salon income", True),
        ("Membership & Package Sales", "income", "revenue", "Salon income", False), ("Other Income", "income", "revenue", "Other income", False),
        ("Vendor Refund / Discount Received", "income", "revenue", "Other income", False),
        (fin.H_TIPS, "income", "tip_in", "Pass-through", True), (fin.H_GST_IN, "income", "gst_in", "Pass-through", True),
        (fin.H_CAPITAL, "income", "equity_in", "Owner", True),
        ("Rent", "expense", "expense", "Premises", False), ("Electricity & Water", "expense", "expense", "Premises", False),
        ("Repairs & Maintenance", "expense", "expense", "Premises", False), ("Housekeeping & Laundry", "expense", "expense", "Premises", False),
        (fin.H_SALARY, "expense", "expense", "People", True), (fin.H_INCENTIVE, "expense", "expense", "People", True),
        (fin.H_REIMB, "expense", "expense", "People", True), ("Staff Welfare (tea, snacks, uniform)", "expense", "expense", "People", False),
        ("Marketing & Ads", "expense", "expense", "Growth", False), ("Software & Subscriptions", "expense", "expense", "Growth", False),
        ("Consumables & Supplies (non-stock)", "expense", "expense", "Operations", False), ("Equipment Purchase", "expense", "expense", "Operations", False),
        (fin.H_PETTY, "expense", "expense", "Operations", True), ("Bank & Card Charges", "expense", "expense", "Finance", False),
        ("Licences & Compliance", "expense", "expense", "Finance", False), ("Professional Fees (CA / Legal)", "expense", "expense", "Finance", False),
        ("Client Refunds", "expense", "expense", "Operations", False),
        (fin.H_TIP_OUT, "expense", "tip_out", "Pass-through", True), (fin.H_VENDOR, "expense", "vendor_pay", "Pass-through", True),
        (fin.H_GST_OUT, "expense", "gst_out", "Pass-through", True), (fin.H_DRAWINGS, "expense", "equity_out", "Owner", True),
    ]
    for n, k, nat, g, sysf in heads:
        db.session.add(Category(name=n, kind=k, nature=nat, group=g, system=sysf))

    # ---------------- accounts ----------------
    accts = {}
    def acc(key, name, typ, br, opening, **kw):
        a = Account(name=name, type=typ, branch_id=br.id if br else None, opening_balance=opening, **kw)
        db.session.add(a); accts[key] = a
    acc("cash_t", "Thakurdwar – Cash Counter", "cash", tdw, 12000)
    acc("cash_s", "Sikka Nagar – Cash Counter", "cash", skn, 8000)
    acc("card_t", "Thakurdwar – Card Machine (settlement)", "card", tdw, 0)
    acc("card_s", "Sikka Nagar – Card Machine (settlement)", "card", skn, 0)
    acc("upi_rk", "RK Personal UPI (rk@upi)", "upi", tdw, 15000, upi_id="rakesh@upi", notes="RK's own UPI – used for most premium clients")
    acc("upi_t", "Thakurdwar – Salon UPI", "upi", tdw, 20000, upi_id="rkhairpro.tdw@upi")
    acc("upi_s", "Sikka Nagar – Salon UPI", "upi", skn, 10000, upi_id="rkhairpro.skn@upi")
    acc("bank_t", "HDFC Current A/c – Company", "bank", None, 250000, bank_name="HDFC Bank", notes="Main company current account")
    acc("bank_s", "Sikka Nagar – Branch Bank A/c", "bank", skn, 40000, bank_name="ICICI Bank")
    db.session.flush()

    if mode == "clean":            # go-live: only the owner, two branches, heads and accounts
        rk = Employee(name="Rakesh Kumar (RK)", mobile="9820000001", role="super_admin", home_branch_id=tdw.id, takes_appointments=True, salary=0, commission_pct=0,
                      designation="Founder & Master Stylist", photo="img/owner_small.jpg", joined_on=today)
        rk.set_password(pw)
        db.session.add(rk)
        for i, n in enumerate(["Hair Cut & Styling", "Hair Colour", "Hair Treatments", "Grooming & Beard", "Skin & Facial", "Waxing & Threading", "Hand & Foot", "Bridal & Occasion"]):
            db.session.add(ServiceCategory(name=n, sort=i))
        for shift, titles in {"opening": ["Switch on lights, AC and music", "Sanitise stations and tools"], "closing": ["Cash counted and day close done in the app", "Tools sterilised and stored"]}.items():
            for t in titles:
                db.session.add(ChecklistItem(shift=shift, title=t, branch_id=None))
        db.session.commit()
        return

    # ---------------- services ----------------
    cats = {}
    for i, n in enumerate(["Hair Cut & Styling", "Hair Colour", "Hair Treatments", "Grooming & Beard", "Skin & Facial", "Waxing & Threading", "Hand & Foot", "Bridal & Occasion"]):
        c = ServiceCategory(name=n, sort=i); db.session.add(c); cats[n] = c
    db.session.flush()
    S = {}
    svc_rows = [
        ("Hair Cut & Styling", "Men's Haircut", "Men", 500, 45), ("Hair Cut & Styling", "Women's Haircut", "Women", 900, 60), ("Hair Cut & Styling", "Kids Haircut", "Unisex", 350, 30),
        ("Hair Cut & Styling", "Blow Dry & Styling", "Women", 700, 45), ("Hair Cut & Styling", "Signature Cut by RK", "Unisex", 1800, 60),
        ("Hair Colour", "Global Colour (Men)", "Men", 1800, 90), ("Hair Colour", "Global Colour (Women)", "Women", 3500, 120), ("Hair Colour", "Root Touch-up", "Unisex", 1200, 60),
        ("Hair Colour", "Highlights / Balayage", "Women", 6500, 180), ("Hair Treatments", "Hair Spa", "Unisex", 1500, 60), ("Hair Treatments", "Keratin Smoothening", "Women", 7500, 180),
        ("Hair Treatments", "Anti-dandruff Treatment", "Unisex", 1400, 45), ("Grooming & Beard", "Beard Trim & Shape", "Men", 300, 30), ("Grooming & Beard", "Shave", "Men", 250, 30),
        ("Grooming & Beard", "Head Massage", "Unisex", 400, 30), ("Skin & Facial", "Classic Facial", "Unisex", 1600, 60), ("Skin & Facial", "Gold Radiance Facial", "Unisex", 2800, 75),
        ("Skin & Facial", "De-Tan Clean-up", "Unisex", 1100, 45), ("Waxing & Threading", "Full Arms Waxing", "Women", 700, 30), ("Waxing & Threading", "Eyebrow Threading", "Women", 100, 15),
        ("Hand & Foot", "Classic Manicure", "Unisex", 800, 45), ("Hand & Foot", "Classic Pedicure", "Unisex", 1000, 60), ("Bridal & Occasion", "Party Makeup", "Women", 4500, 90),
        ("Bridal & Occasion", "Groom Grooming Package", "Men", 6000, 150),
    ]
    for cn, n, g, p, d in svc_rows:
        s = Service(category_id=cats[cn].id, name=n, gender=g, price=p, duration=d, description=f"{n} – premium service"); db.session.add(s); S[n] = s
    db.session.flush()

    # ---------------- vendors / items ----------------
    vendors = {}
    for n, c in [("L'Oréal Professionnel Distributor", "Hair care & colour"), ("Wella Salon Supplies", "Colour & treatment"), ("Beauty Bazaar Wholesale", "Disposables"), ("Kerastase Mumbai", "Premium hair care"), ("Salon Tools India", "Tools & equipment")]:
        v = Vendor(name=n, category=c, contact="Sales desk", mobile="98" + str(RND.randint(10000000, 99999999)), terms="30 days" if "Wholesale" not in n else "Cash"); db.session.add(v); vendors[n] = v
    db.session.flush()
    V = list(vendors.values())
    items = {}
    item_rows = [
        ("Shampoo (Backbar)", "Hair care", "ml", 0.9, 0, False, 0), ("Conditioner (Backbar)", "Hair care", "ml", 1.1, 0, False, 0), ("Hair Colour Tube", "Colour", "g", 4.5, 0, False, 1),
        ("Developer 20 Vol", "Colour", "ml", 0.6, 0, False, 1), ("Bleach Powder", "Colour", "g", 2.2, 0, False, 1), ("Keratin Solution", "Treatment", "ml", 9.0, 0, False, 3),
        ("Hair Spa Cream", "Treatment", "g", 3.0, 0, False, 0), ("Facial Kit – Gold", "Skin", "pcs", 320, 0, False, 2), ("Facial Kit – Classic", "Skin", "pcs", 180, 0, False, 2),
        ("Wax (Rica) Cartridge", "Skin", "pcs", 260, 0, False, 2), ("Disposable Neck Strips", "Disposable", "pcs", 0.6, 0, False, 2), ("Disposable Towels", "Disposable", "pcs", 4, 0, False, 2),
        ("Gloves (Pair)", "Disposable", "pcs", 6, 0, False, 2), ("Beard Oil 30ml (Retail)", "Retail", "pcs", 320, 650, True, 3), ("Argan Hair Serum 100ml (Retail)", "Retail", "pcs", 540, 1100, True, 0),
        ("Anti-Frizz Shampoo 250ml (Retail)", "Retail", "pcs", 480, 950, True, 0), ("Styling Wax 75g (Retail)", "Retail", "pcs", 260, 550, True, 3), ("Razor Blades (pack)", "Grooming", "pcs", 45, 0, False, 4),
    ]
    for n, cat_, u, cost, sell, retail, vi in item_rows:
        it = Item(name=n, sku=n[:3].upper() + str(RND.randint(100, 999)), category=cat_, unit=u, cost=cost, sell_price=sell, is_retail=retail, vendor_id=V[vi].id); db.session.add(it); items[n] = it
    db.session.flush()
    recipes = {"Men's Haircut": [("Shampoo (Backbar)", 20), ("Disposable Neck Strips", 1)], "Women's Haircut": [("Shampoo (Backbar)", 30), ("Conditioner (Backbar)", 20), ("Disposable Neck Strips", 1)],
               "Signature Cut by RK": [("Shampoo (Backbar)", 30), ("Conditioner (Backbar)", 20), ("Disposable Neck Strips", 1)], "Global Colour (Men)": [("Hair Colour Tube", 40), ("Developer 20 Vol", 60), ("Gloves (Pair)", 1)],
               "Global Colour (Women)": [("Hair Colour Tube", 80), ("Developer 20 Vol", 120), ("Gloves (Pair)", 1)], "Root Touch-up": [("Hair Colour Tube", 30), ("Developer 20 Vol", 45), ("Gloves (Pair)", 1)],
               "Highlights / Balayage": [("Bleach Powder", 60), ("Developer 20 Vol", 120), ("Hair Colour Tube", 30), ("Gloves (Pair)", 1)], "Hair Spa": [("Hair Spa Cream", 40), ("Shampoo (Backbar)", 20), ("Disposable Towels", 1)],
               "Keratin Smoothening": [("Keratin Solution", 60), ("Shampoo (Backbar)", 30), ("Gloves (Pair)", 1)], "Classic Facial": [("Facial Kit – Classic", 1), ("Disposable Towels", 1)],
               "Gold Radiance Facial": [("Facial Kit – Gold", 1), ("Disposable Towels", 1)], "Full Arms Waxing": [("Wax (Rica) Cartridge", 0.25)], "Shave": [("Razor Blades (pack)", 0.1), ("Disposable Neck Strips", 1)],
               "Beard Trim & Shape": [("Disposable Neck Strips", 1)]}
    for sn, lst in recipes.items():
        for iname, q in lst:
            db.session.add(ServiceConsumable(service_id=S[sn].id, item_id=items[iname].id, qty=q))

    # opening stock on the day before FY start, with reorder levels
    open_day = fy - timedelta(days=1)
    for br in (tdw, skn):
        for n, it in items.items():
            base = {"ml": 6000, "g": 4000, "pcs": 60}[it.unit] if not it.is_retail else 25
            base = {"Disposable Neck Strips": 500, "Disposable Towels": 250, "Gloves (Pair)": 200}.get(n, base)
            if it.name.startswith("Facial Kit") or it.name.startswith("Wax"):
                base = 30
            qty = round(base * (1.0 if br is tdw else 0.8), 1)
            level = inv_logic.level(it.id, br.id)
            level.reorder_level = round(base * 0.22, 1)
            inv_logic.move(it, br.id, "opening", qty, open_day, note="Opening stock", alert=False)

    # ---------------- employees ----------------
    def emp(name, mobile, role, br, weekly_off, **kw):
        e = Employee(name=name, mobile=mobile, role=role, branch_id=br.id if br and role != "super_admin" else None, home_branch_id=br.id if br else None,
                     weekly_off=weekly_off, salary=kw.pop("salary", 18000), joined_on=kw.pop("joined_on", date(2024, 4, 1)), **kw)
        e.set_password(pw); db.session.add(e); return e
    rk = emp("Rakesh Kumar (RK)", "9820000001", "super_admin", tdw, 0, designation="Founder & Master Stylist", takes_appointments=True, salary=0, commission_pct=0,
             bio="Founder & master stylist – appointment only", photo="img/owner_small.jpg", monthly_target=350000, email="rk@rkhairpro.example", tip_mode="daily", joined_on=date(2019, 1, 1),
             address="South Mumbai", aadhaar="XXXX-XXXX-1001", pan="ABCPK1001A", dob=date(1988, 6, 14))
    db.session.flush()
    t_admin = emp("Sanjay Pawar", "9820000011", "branch_admin", tdw, 2, designation="Branch Manager – Thakurdwar", reports_to_id=rk.id, takes_appointments=False, tip_mode="salary")
    t_recp = emp("Neha Shah", "9820000012", "reception", tdw, 3, designation="Front Desk", reports_to_id=t_admin.id)
    t_sr = emp("Imran Sheikh", "9820000013", "senior_stylist", tdw, 1, designation="Senior Stylist", reports_to_id=t_admin.id, takes_appointments=True, bio="Colour & precision cuts", monthly_target=140000)
    t_st = emp("Pooja Iyer", "9820000014", "stylist", tdw, 2, designation="Stylist", reports_to_id=t_sr.id, takes_appointments=True, bio="Women's styling & skin", monthly_target=110000)
    t_as1 = emp("Rohit Jadhav", "9820000015", "assistant", tdw, 3, designation="Stylist Assistant", reports_to_id=t_sr.id, tip_mode="daily", monthly_target=60000)
    t_as2 = emp("Sana Khan", "9820000016", "assistant", tdw, 4, designation="Beauty Assistant", reports_to_id=t_st.id, tip_mode="salary", monthly_target=60000)
    s_admin = emp("Vikas Naik", "9820000021", "branch_admin", skn, 1, designation="Branch Manager – Sikka Nagar", reports_to_id=rk.id, tip_mode="salary")
    s_recp = emp("Aarti Desai", "9820000022", "reception", skn, 2, designation="Front Desk", reports_to_id=s_admin.id)
    s_sr = emp("Farhan Qureshi", "9820000023", "senior_stylist", skn, 3, designation="Senior Stylist", reports_to_id=s_admin.id, takes_appointments=True, bio="Grooming & beard specialist", monthly_target=130000)
    s_st = emp("Kavita Rao", "9820000024", "stylist", skn, 4, designation="Stylist", reports_to_id=s_sr.id, takes_appointments=True, bio="Hair colour & spa", monthly_target=100000)
    s_as1 = emp("Deepak Yadav", "9820000025", "assistant", skn, 0, designation="Stylist Assistant", reports_to_id=s_sr.id, tip_mode="daily", monthly_target=55000)
    s_as2 = emp("Mansi Pawar", "9820000026", "assistant", skn, 2, designation="Beauty Assistant", reports_to_id=s_st.id, tip_mode="salary", monthly_target=55000)
    for e in (t_recp, s_recp):
        e.public_visible = False
    db.session.flush()
    branch_staff = {tdw.id: [t_sr, t_st, t_as1, t_as2, rk], skn.id: [s_sr, s_st, s_as1, s_as2]}
    stylists = {tdw.id: [rk, t_sr, t_st], skn.id: [s_sr, s_st]}

    # branch default checklists
    for shift, titles in {"opening": ["Switch on lights, AC and music", "Sanitise stations and tools", "Restock towels and disposables", "Check appointment list for the day", "Count opening cash in counter"],
                          "closing": ["Cash counted and day close done in the app", "Tips paid out / recorded", "Tools sterilised and stored", "Floor cleaned, waste cleared", "All lights, AC and machines switched off"],
                          "hygiene": ["Restroom cleaned and stocked", "Client waiting area cleaned", "Mirrors and chairs wiped"]}.items():
        for t in titles:
            db.session.add(ChecklistItem(shift=shift, title=t, branch_id=None))
    for br in (tdw, skn):
        for n, cost, yrs in [("Hydraulic Styling Chairs (6)", 84000, 2), ("Hair Dryer Stations", 36000, 1), ("Hair Steamer", 18000, 1), ("Card Payment Machine", 9000, 1), ("Air Conditioner (4 ton)", 96000, 3)]:
            pd = today - timedelta(days=365 * yrs)
            db.session.add(Equipment(name=n, branch_id=br.id, purchase_date=pd, cost=cost, warranty_till=pd + timedelta(days=730), last_service=today - timedelta(days=RND.randint(40, 150)),
                                     next_service=today + timedelta(days=RND.randint(-5, 90)), status="working"))
    for hd, nm in [(date(today.year, 8, 15), "Independence Day"), (date(today.year, 10, 20), "Diwali (sample)"), (date(today.year, 12, 25), "Christmas")]:
        db.session.add(Holiday(date=hd, name=nm))
    db.session.add(Coupon(code="WELCOME10", description="10% off for first-time clients", kind="percent", value=10, min_bill=800, max_discount=500, valid_from=fy, valid_to=today + timedelta(days=180)))
    db.session.add(Coupon(code="FESTIVE500", description="Rs.500 off on bills above Rs.3,000", kind="flat", value=500, min_bill=3000, valid_from=today - timedelta(days=10), valid_to=today + timedelta(days=45)))
    db.session.add(Coupon(code="BDAYSPL", description="Birthday month special – 15% off", kind="percent", value=15, min_bill=1000, max_discount=1000, valid_from=fy, valid_to=today + timedelta(days=300)))
    db.session.flush()
    # RK works from Sikka Nagar on a couple of upcoming days (demo of per-day branch choice)
    nxt = today + timedelta(days=(3 - today.weekday()) % 7 or 7)
    db.session.add(EmployeeDayBranch(employee_id=rk.id, date=nxt, branch_id=skn.id, note="Guest day at Sikka Nagar"))

    # ---------------- clients ----------------
    first = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rahul", "Karan", "Rohan", "Siddharth", "Nikhil", "Manish", "Ananya", "Diya", "Isha", "Meera", "Priya", "Riya", "Sneha", "Tanvi", "Kritika", "Shreya", "Neelam", "Pallavi",
             "Amit", "Sameer", "Yash", "Harsh", "Devansh", "Kabir", "Zoya", "Aisha", "Fatima", "Sunita", "Rekha", "Jignesh", "Hitesh", "Dhaval", "Mihir", "Bhavin", "Payal", "Komal"]
    last = ["Mehta", "Shah", "Desai", "Joshi", "Patel", "Sharma", "Kapoor", "Malhotra", "Gupta", "Jain", "Kothari", "Parekh", "Doshi", "Bhatia", "Sethi", "Nair", "Iyer", "Menon", "Khanna", "Vora"]
    sources = ["Walk-in", "Instagram", "Website", "Phone", "Referral", "Google", "Instagram", "Referral"]
    clients = []
    used = set()
    for i in range(520):
        while True:
            n = f"{RND.choice(first)} {RND.choice(last)}"
            if n not in used:
                used.add(n); break
        g = "Female" if first.index(n.split()[0]) in range(10, 22) or n.split()[0] in ("Zoya", "Aisha", "Fatima", "Sunita", "Rekha", "Payal", "Komal") else "Male"
        while True:
            mob = str(9000000000 + RND.randint(10000000, 99999999))
            if mob not in used:
                used.add(mob); break
        c = Client(name=n, mobile=mob, gender=g, source=RND.choice(sources), branch_id=RND.choice([tdw.id, tdw.id, skn.id]),
                   dob=date(RND.randint(1975, 2004), RND.randint(1, 12), RND.randint(1, 28)), created_at=datetime.combine(fy, datetime.min.time()) + timedelta(days=RND.randint(0, (today - fy).days)))
        if RND.random() < 0.15:
            c.anniversary = date(RND.randint(2008, 2024), RND.randint(1, 12), RND.randint(1, 28))
        clients.append(c); db.session.add(c)
    # a few birthdays this month for the marketing demo
    for c in RND.sample(clients, 6):
        c.dob = date(1990, today.month, RND.randint(1, 28))
    db.session.flush()

    # ---------------- history: attendance, bills, expenses ----------------
    if history:
        _history(locals(), today, fy)

    db.session.commit()


def _history(L, today, fy):
    tdw, skn = L["tdw"], L["skn"]
    accts, S, items, clients = L["accts"], L["S"], L["items"], L["clients"]
    branch_staff, stylists = L["branch_staff"], L["stylists"]
    rk = L["rk"]
    all_emps = Employee.query.filter(Employee.role != "super_admin").all()
    svc_list = list(S.values())
    men = [S[n] for n in ["Men's Haircut", "Beard Trim & Shape", "Shave", "Head Massage", "Global Colour (Men)", "Hair Spa", "Classic Facial", "Anti-dandruff Treatment", "Signature Cut by RK"]]
    women = [S[n] for n in ["Women's Haircut", "Blow Dry & Styling", "Global Colour (Women)", "Root Touch-up", "Hair Spa", "Classic Facial", "Gold Radiance Facial", "Full Arms Waxing", "Eyebrow Threading",
                            "Classic Manicure", "Classic Pedicure", "Keratin Smoothening", "Highlights / Balayage"]]
    weights_women = [8, 5, 3, 3, 4, 3, 2, 3, 4, 2, 2, 1, 1]
    weights_men = [10, 6, 4, 3, 2, 2, 2, 1, 2]
    retail = [i for i in items.values() if i.is_retail]
    upi_for = {tdw.id: [accts["upi_rk"], accts["upi_t"], accts["upi_t"]], skn.id: [accts["upi_s"]]}

    client_w = [6 if i % 6 == 0 else 1 for i in range(len(clients))]      # ~1 in 6 clients are regulars
    codes = set()
    now_ = datetime.now()
    d = fy
    day_index = 0
    while d <= today:
        is_today = d == today
        for br in (tdw, skn):
            busy = 1.0 if br is tdw else 0.75
            wd = d.weekday()
            n_bills = int(RND.gauss(9 if wd < 5 else 14, 2) * busy)
            if is_today:                 # today is partly done: only the hours that have passed
                n_bills = int(n_bills * min(1.0, max(0.0, (now_.hour + now_.minute / 60 - 10) / 11)))
            staff = [e for e in branch_staff[br.id] if e.location_on(d) == br.id and e.weekly_off != wd]
            if not staff:
                continue
            for _ in range(max(n_bills, 2 if not is_today else 0)):
                cl = RND.choices(clients, client_w)[0]
                is_f = cl.gender == "Female"
                pool, w = (women, weights_women) if is_f else (men, weights_men)
                chosen = list({RND.choices(pool, w)[0] for _ in range(RND.choice([1, 1, 2, 2, 3]))})
                primary = RND.choice([e for e in staff if e.role in ("senior_stylist", "stylist") or e is rk] or staff)
                asst = [e for e in staff if e.role == "assistant"]
                lines = []
                for s in chosen:
                    who = primary if RND.random() < 0.75 or not asst else RND.choice(asst)
                    lines.append(dict(kind="service", ref_id=s.id, qty=1, price=s.price * (1.25 if who is rk else 1.0), employee_id=who.id))
                if RND.random() < 0.08 and retail:
                    r = RND.choice(retail)
                    lines.append(dict(kind="product", ref_id=r.id, qty=1, price=r.sell_price, employee_id=primary.id))
                subtotal = sum(l["qty"] * l["price"] for l in lines)
                tips = []
                if RND.random() < 0.55:
                    tip = RND.choice([50, 100, 100, 150, 200, 300, 500]) if subtotal > 700 else RND.choice([20, 30, 50, 100])
                    tw = RND.choice(asst or [primary]) if RND.random() < 0.6 else primary
                    tips.append(dict(employee_id=tw.id, amount=tip))
                disc_pct = RND.choice([0, 0, 0, 0, 5, 10])
                total = round(subtotal - round(subtotal * disc_pct / 100.0, 2) + sum(t["amount"] for t in tips), 2)
                mode = RND.choices(["cash", "upi", "card"], [3, 6, 2])[0]
                pay = dict(mode=mode, amount=round(total, 2))
                if mode == "upi":
                    pay["account_id"] = RND.choice(upi_for[br.id]).id
                elif mode == "card":
                    pay["account_id"] = accts["card_t" if br is tdw else "card_s"].id
                else:
                    pay["account_id"] = accts["cash_t" if br is tdw else "cash_s"].id
                sp = db.session.begin_nested()
                try:
                    inv = billing.create_invoice(br.id, cl, lines, tips, [pay], on_date=d, discount_pct=disc_pct, by_id=(L["t_recp"] if br is tdw else L["s_recp"]).id, send_whatsapp=False)
                    # spread time-of-day for peak-hour report
                    hrs = [h for h in range(10, 21) if not is_today or h <= max(10, now_.hour)]
                    wts = [w for h, w in zip(range(10, 21), [3, 5, 6, 5, 4, 5, 7, 8, 7, 4, 2]) if h in hrs]
                    inv.created_at = datetime.combine(d, datetime.min.time()) + timedelta(hours=RND.choices(hrs, wts)[0], minutes=RND.randint(0, 59))
                    if inv.created_at > now_:
                        inv.created_at = now_ - timedelta(minutes=RND.randint(1, 30))
                    if RND.random() < 0.42:          # this visit came through an appointment
                        st_ = inv.created_at.replace(minute=RND.choice([0, 30]), second=0)
                        code = "RK" + "".join(RND.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
                        if code not in codes:
                            codes.add(code)
                            ap = Appointment(code=code, branch_id=br.id, client_id=cl.id, employee_id=primary.id, start_dt=st_, end_dt=st_ + timedelta(minutes=sum(x.duration for x in chosen)),
                                             status="completed", source=RND.choices(["online", "website", "instagram", "phone", "offline", "walk-in"], [24, 12, 14, 22, 18, 10])[0],
                                             created_by_id=(L["t_recp"] if br is tdw else L["s_recp"]).id, created_at=st_ - timedelta(days=RND.randint(0, 4), hours=RND.randint(1, 20)),
                                             reminder_sent=True)
                            for x in chosen:
                                ap.lines.append(AppointmentService(service_id=x.id, price=x.price, duration=x.duration))
                            db.session.add(ap)
                            db.session.flush()
                            inv.appointment_id, ap.invoice_id = ap.id, inv.id
                    sp.commit()
                except billing.BillError:
                    sp.rollback()
                    continue
            # attendance
            for e in staff:
                if e is rk:
                    continue
                r = RND.random()
                if r < 0.035 and not is_today:
                    continue                      # absent (no record)
                inn = dt(d, e.shift_start); out = dt(d, e.shift_end)
                if is_today and (now_.hour * 60 + now_.minute) < int(e.shift_start[:2]) * 60 + int(e.shift_start[3:]):
                    continue                      # shift hasn't started yet
                if r < 0.10:
                    inn += timedelta(minutes=RND.randint(20, 70))       # late
                else:
                    inn += timedelta(minutes=RND.randint(-15, 10))
                if 0.10 <= r < 0.135:
                    out -= timedelta(minutes=RND.randint(45, 120))       # early exit
                else:
                    out += timedelta(minutes=RND.randint(0, 40))
                if 0.135 <= r < 0.165:
                    out = inn + timedelta(hours=5, minutes=RND.randint(0, 50))  # half day
                a = Attendance(employee_id=e.id, date=d, check_in=inn, check_out=None if is_today else out, method="face" if RND.random() < 0.9 else "manual")
                if not is_today:
                    pr.set_out_metrics(a, e)
                else:
                    a.status = "present"
                late = (inn.hour * 60 + inn.minute) - (int(e.shift_start[:2]) * 60 + int(e.shift_start[3:]))
                a.late_min = late if late > 15 else 0
                db.session.add(a)
        # fixed expenses & petty cash
        if d.day == 1 and day_index > 0 or d == fy:
            _monthly_expenses(L, d)
        if RND.random() < 0.55:
            _petty(L, d)
        # end of day tip payout for "daily" employees
        if not is_today and (d.weekday() != 6 or RND.random() < 0.5):
            _daily_tip_payout(L, d)
        if d.day in (5, 12, 19, 26) and not is_today:
            _replenish(L, d)
        if d.weekday() == 5 and not is_today:           # weekly sweep: cash deposit + card / UPI settlement to the company bank
            bm = fin.balance_map(d)
            for br, key, keep, note in ((tdw, "cash_t", 15000, "Cash deposited to bank"), (skn, "cash_s", 12000, "Cash deposited to bank"), (tdw, "card_t", 0, "Card settlement credited by bank"),
                                        (skn, "card_s", 0, "Card settlement credited by bank"), (tdw, "upi_t", 20000, "UPI balance swept to bank"), (skn, "upi_s", 10000, "UPI balance swept to bank"),
                                        (tdw, "upi_rk", 15000, "UPI balance swept to bank")):
                bal = bm[accts[key].id]
                if bal - keep > 5000:
                    amt = round((bal - keep) / 1000) * 1000
                    db.session.add(Transaction(kind="transfer", amount=amt, date=d, account_id=accts[key].id, to_account_id=accts["bank_t"].id, branch_id=br.id,
                                               mode="cash" if key.startswith("cash") else "bank", notes=note, batch=f"SWEEP-{d}"))
        day_index += 1
        d += timedelta(days=1)
        if day_index % 20 == 0:
            db.session.flush()
    db.session.flush()

    # leaves / approvals for demo
    e = L["t_st"]
    db.session.add(LeaveRequest(employee_id=e.id, from_date=today - timedelta(days=40), to_date=today - timedelta(days=39), kind="paid", reason="Family function", status="approved", decided_by_id=L["t_admin"].id))
    db.session.add(LeaveRequest(employee_id=L["s_st"].id, from_date=today + timedelta(days=6), to_date=today + timedelta(days=7), kind="paid", reason="Sister's wedding", status="pending"))
    db.session.add(LeaveRequest(employee_id=L["t_as2"].id, from_date=today + timedelta(days=12), to_date=today + timedelta(days=12), kind="unpaid", reason="Personal work", status="pending"))
    db.session.add(RoleRequest(employee_id=L["t_as1"].id, from_role="assistant", to_role="stylist", reason="Handles independent clients for 6 months now", requested_by_id=L["t_admin"].id))
    db.session.add(RoleRequest(employee_id=L["s_as1"].id, from_role="assistant", to_role="senior_stylist", reason="Certified in advanced colour course", requested_by_id=L["s_admin"].id, status="pending"))
    db.session.add(StockRequest(item_id=items["Hair Colour Tube"].id, branch_id=skn.id, qty=3000, urgency="high", note="Colour services heavy this week", status="pending", requested_by_id=L["s_admin"].id))
    db.session.add(Announcement(title="Diwali roster & bonus policy", body="Diwali week timings will be 9 AM – 10 PM. Please submit leave requests before 30 September. Festival bonus details will be shared by the owner.", pinned=True, created_by_id=rk.id))
    db.session.add(Announcement(title="Hygiene first", body="Every tool must be sterilised between clients. Branch managers will run random checks every week using the daily checklist.", created_by_id=rk.id))
    db.session.add(Task(title="Renew fire-safety certificate", detail="Contact society office; upload receipt", branch_id=tdw.id, assigned_to_id=L["t_admin"].id, created_by_id=rk.id, due_date=today + timedelta(days=9), priority="high"))
    db.session.add(Task(title="Reorganise backbar shelves by category", branch_id=skn.id, assigned_to_id=L["s_as1"].id, created_by_id=L["s_admin"].id, due_date=today + timedelta(days=3)))
    for src, nm, intr, stt in [("Instagram", "Tanya Bhatt", "Balayage consult", "new"), ("Instagram", "Rishi Mehra", "Groom package (Dec)", "contacted"), ("Website", "Payal Shah", "Keratin", "booked"), ("Google", "Ketan Doshi", "Beard styling", "contacted"),
                               ("Walk-in", "Mrs. Kapadia", "Bridal trial", "new"), ("Phone", "Aashna Jain", "Party makeup", "lost")]:
        db.session.add(Lead(name=nm, mobile=str(9100000000 + RND.randint(1000000, 9999999)), source=src, interest=intr, branch_id=tdw.id, status=stt, follow_up=today + timedelta(days=RND.randint(0, 5)),
                            created_at=datetime.now() - timedelta(days=RND.randint(0, 20))))
    # feedback (some received)
    for f in Feedback.query.all():
        inv = db.session.get(Invoice, f.invoice_id)
        f.created_at = datetime.combine(inv.date, datetime.min.time()) + timedelta(hours=19)
        if RND.random() < 0.4:
            f.rating = RND.choices([5, 4, 3, 2, 1], [10, 6, 2, 1, 1])[0]
            f.comment = {5: "Loved the cut, great service!", 4: "Good experience overall.", 3: "Okay, waiting time was long.", 2: "Not happy with the finish.", 1: "Rude behaviour at counter."}[f.rating]
            f.status = "received"; f.submitted_at = f.created_at + timedelta(hours=RND.randint(1, 20))
            if f.submitted_at > datetime.now():
                f.submitted_at = datetime.now()
    db.session.add(Campaign(name="Diwali early-bird offer", segment="vip", message="Hi {name}, enjoy FESTIVE500 – Rs.500 off on bills above Rs.3,000 this festive season. Book: {book_link}", status="sent", sent_count=61,
                            sent_at=datetime.now() - timedelta(days=12), created_at=datetime.now() - timedelta(days=12), created_by_id=rk.id))
    db.session.add(Campaign(name="We miss you – 10% off", segment="lapsed", message="Hi {name}, it's been a while! Use WELCOME10 for 10% off your next visit.", status="sent", sent_count=87,
                            sent_at=datetime.now() - timedelta(days=30), created_at=datetime.now() - timedelta(days=30), created_by_id=rk.id))
    for iname, br, qty, back in (("Shampoo (Backbar)", tdw, 300, 25), ("Facial Kit – Gold", skn, 1, 12), ("Hair Colour Tube", tdw, 60, 40), ("Wax (Rica) Cartridge", tdw, 1, 8), ("Developer 20 Vol", skn, 240, 33)):
        inv_logic.move(items[iname], br.id, "wastage", qty, today - timedelta(days=back), note=RND.choice(["Spilled during service", "Expired / damaged", "Opened tube dried up"]), by_id=L["t_admin"].id, alert=False)
    # today + upcoming appointments
    _appointments(L, today)
    # payroll for the last completed month generated & the previous one paid
    db.session.flush()
    months, m = [], fy
    while m < today.replace(day=1):
        months.append(m.strftime("%Y-%m"))
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    for ym in months[:-1]:                       # every completed month except the latest is already paid
        d1, d2 = month_bounds(ym)
        paid_on = min(today - timedelta(days=1), d2 + timedelta(days=6))
        for p in pr.generate_payroll(ym, [tdw.id, skn.id]):
            pr.pay_payroll(p, "bank", accts["bank_t"].id, paid_on, L["rk"].id)
        db.session.flush()
    if months:
        pr.generate_payroll(months[-1], [tdw.id, skn.id])       # latest month left pending so the demo can pay it
    db.session.add(Reimbursement(employee_id=L["t_as1"].id, branch_id=tdw.id, date=today - timedelta(days=1), amount=240, description="Bought tea/coffee for VIP clients", category_id=fin.cat("Staff Welfare (tea, snacks, uniform)").id, settle_mode="daily", status="pending"))
    db.session.add(Reimbursement(employee_id=L["s_as2"].id, branch_id=skn.id, date=today - timedelta(days=2), amount=620, description="Auto fare to pick up stock", category_id=fin.cat(fin.H_PETTY).id, settle_mode="salary", status="approved"))
    db.session.add(Reimbursement(employee_id=L["t_sr"].id, branch_id=tdw.id, date=today - timedelta(days=4), amount=900, description="Emergency purchase of styling clips", category_id=fin.cat("Consumables & Supplies (non-stock)").id, settle_mode="salary", status="pending"))
    # RK capital / drawings example
    db.session.add(Transaction(kind="in", amount=200000, date=fy, category_id=fin.cat(fin.H_CAPITAL).id, account_id=accts["bank_t"].id, mode="bank", branch_id=None, notes="Working capital introduced by owner (sample)"))
    for m in range(2):
        db.session.add(Notification(employee_id=rk.id, title="Welcome to RK Hair Pro", body="Your central dashboard is ready. Explore branches, appointments, finance and reports.", kind="info"))
    db.session.flush()
    # low stock demo: drop a couple of items below reserve
    for iname, br in (("Hair Colour Tube", skn), ("Disposable Towels", tdw)):
        it = items[iname]
        sl = inv_logic.level(it.id, br.id)
        target = max(sl.reorder_level - 5, 1)
        if sl.qty > target:
            inv_logic.move(it, br.id, "consumption", sl.qty - target, today - timedelta(days=1), note="Heavy usage", alert=True)


def _monthly_expenses(L, d):
    tdw, skn, accts = L["tdw"], L["skn"], L["accts"]
    rows = [(tdw, "Rent", 165000, "bank_t", "bank", "Landlord – Thakurdwar premises"), (skn, "Rent", 120000, "bank_t", "bank", "Landlord – Sikka Nagar premises"),
            (tdw, "Electricity & Water", RND.randint(26000, 34000), "bank_t", "bank", "MSEDCL / BMC"), (skn, "Electricity & Water", RND.randint(19000, 26000), "bank_t", "bank", "MSEDCL / BMC"),
            (tdw, "Marketing & Ads", RND.randint(12000, 22000), "upi_t", "upi", "Instagram & Google Ads"), (skn, "Marketing & Ads", RND.randint(6000, 12000), "upi_s", "upi", "Local flyers & Instagram"),
            (tdw, "Software & Subscriptions", 1999, "bank_t", "bank", "Salon app + WhatsApp API"), (tdw, "Housekeeping & Laundry", RND.randint(6000, 9000), "cash_t", "cash", "Housekeeping staff & towel laundry"),
            (skn, "Housekeeping & Laundry", RND.randint(4500, 7500), "cash_s", "cash", "Housekeeping staff & towel laundry"), (tdw, "Bank & Card Charges", RND.randint(1500, 3200), "bank_t", "bank", "Card MDR & bank charges"),
            (tdw, "Repairs & Maintenance", RND.choice([0, 0, 3500, 6800]), "cash_t", "cash", "Plumbing/AC service"),
            (tdw, "Staff Welfare (tea, snacks, uniform)", RND.randint(5500, 7500), "cash_t", "cash", "Tea, snacks, uniforms"), (skn, "Staff Welfare (tea, snacks, uniform)", RND.randint(3500, 5000), "cash_s", "cash", "Tea, snacks, uniforms"),
            (tdw, "Professional Fees (CA / Legal)", 8000, "bank_t", "bank", "CA – monthly compliance"),
            (tdw, "Consumables & Supplies (non-stock)", RND.randint(4000, 8000), "cash_t", "cash", "Stationery, cleaning & misc supplies"),
            (skn, "Consumables & Supplies (non-stock)", RND.randint(3000, 6000), "cash_s", "cash", "Stationery, cleaning & misc supplies"),
            (tdw, "Equipment Purchase", RND.choice([0, 0, 0, 14500, 22000]), "bank_t", "bank", "Salon Tools India")]
    for br, head, amt, acct, mode, party in rows:
        if amt:
            fin.record("out", amt, fin.cat(head), accts[acct], d, br.id, mode=mode if mode != "bank" else "bank", party=party, notes=f"{head} – {d.strftime('%b %Y')}")
    if d.day == 1:       # owner draws a monthly amount from the company account (not a business expense)
        fin.record("out", 150000, fin.cat(fin.H_DRAWINGS), accts["bank_t"], d, None, mode="bank", party="Rakesh Kumar", notes="Owner drawings (sample)")


def _petty(L, d):
    accts = L["accts"]
    br = RND.choice([L["tdw"], L["skn"]])
    ca = accts["cash_t" if br is L["tdw"] else "cash_s"]
    for _ in range(RND.randint(1, 3)):
        what, amt = RND.choice([("Tea, coffee & biscuits for clients", 180), ("Auto fare – stock pickup", 120), ("Courier charges", 250), ("Stationery & printouts", 150), ("Pooja flowers", 100),
                                ("Drinking water cans", 140), ("Cleaning material", 320), ("Battery cells / bulbs", 210)])
        fin.record("out", amt + RND.choice([0, 20, 40]), fin.cat(fin.H_PETTY), ca, d, br.id, mode="cash", notes=what, party="Local shop", petty=True, created_by_id=(L["t_recp"] if br is L["tdw"] else L["s_recp"]).id)


def _daily_tip_payout(L, d):
    for e in Employee.query.filter(Employee.tip_mode == "daily", Employee.role.in_(("assistant", "stylist", "senior_stylist"))).all():
        bal = fin.tip_balance(e.id, upto=d)
        if bal > 0:
            frac = 1.0 if RND.random() < 0.85 else 0.5
            amt = round(bal * frac / 10) * 10 or bal
            fin.pay_tips(e, min(amt, bal), "cash", None, e.branch_id, d, silent=True, note="End-of-day tip payout")


def _replenish(L, d):
    """Stock-driven purchasing: top items back up to their par level, one bill per vendor."""
    tdw, accts = L["tdw"], L["accts"]
    for br in (L["tdw"], L["skn"]):
        by_v = {}
        for sl in StockLevel.query.filter(StockLevel.branch_id == br.id, StockLevel.qty <= StockLevel.reorder_level * 2.2).all():
            by_v.setdefault(sl.item.vendor_id, []).append(sl)
        for vid, sls in by_v.items():
            bill = PurchaseBill(vendor_id=vid, branch_id=br.id, bill_no=f"INV{RND.randint(1000, 9999)}", date=d, note="Regular replenishment")
            db.session.add(bill)
            db.session.flush()
            total = 0
            for sl in sls:
                it = sl.item
                par = sl.reorder_level / 0.22 * 1.5
                q = max(round(par - sl.qty), 1)
                inv_logic.move(it, br.id, "purchase", q, d, unit_cost=it.cost, vendor_id=vid, bill_id=bill.id, note=f"Bill {bill.bill_no}", alert=False)
                total += q * it.cost
            bill.total = round(total, 2)
            pay_now = bill.total if RND.random() < 0.7 else round(bill.total * 0.5, -2)
            if pay_now:
                mode = RND.choice(["upi", "cash", "bank"])
                acct = accts["cash_t" if br is tdw else "cash_s"] if mode == "cash" and bill.total < 8000 else (accts["upi_t"] if mode == "upi" else accts["bank_t"])
                mode = "bank" if acct is accts["bank_t"] else ("upi" if acct.type == "upi" else "cash")
                fin.record("out", pay_now, fin.cat(fin.H_VENDOR), acct, d, br.id, mode="cheque" if mode == "bank" and RND.random() < 0.5 else mode, party=bill.vendor.name if bill.vendor else "", vendor_id=vid,
                           notes=f"Payment for bill {bill.bill_no}")
                bill.paid = pay_now


def _appointments(L, today):
    tdw, skn, S = L["tdw"], L["skn"], L["S"]
    clients = L["clients"]
    svc_by_name = S
    now_ = datetime.now()
    plans = []
    # today & the next 6 days for both branches
    for off in range(0, 7):
        d = today + timedelta(days=off)
        for br in (tdw, skn):
            emps = appt_logic.bookable_employees(br.id, d)
            for e in emps:
                t = 10 * 60 + RND.choice([0, 30])
                for _ in range(RND.randint(2, 5) if off < 2 else RND.randint(1, 3)):
                    names = RND.choice([["Men's Haircut"], ["Men's Haircut", "Beard Trim & Shape"], ["Women's Haircut"], ["Global Colour (Women)"], ["Hair Spa"], ["Classic Facial"], ["Signature Cut by RK"], ["Blow Dry & Styling"], ["Global Colour (Men)"]])
                    ss = [svc_by_name[n] for n in names]
                    dur = sum(s.duration for s in ss)
                    start = datetime(d.year, d.month, d.day) + timedelta(minutes=t)
                    if start.hour * 60 + start.minute + dur > 21 * 60:
                        break
                    try:
                        cl = RND.choice(clients)
                        a = appt_logic.create_appointment(br.id, cl, start, [s.id for s in ss], e.id, source=RND.choice(["online", "website", "instagram", "phone", "offline", "walk-in"]),
                                                          created_by_id=L["t_recp"].id, override_conflict=True)
                        a.status = "confirmed" if RND.random() < 0.6 else "booked"
                        a.created_at = min(now_ - timedelta(hours=RND.randint(3, 90)), start - timedelta(hours=2))
                        if start < now_ - timedelta(hours=1):
                            a.status = "completed"
                        elif start < now_:
                            a.status = "in_service"
                        a.reminder_sent = start < now_ + timedelta(hours=1)
                    except appt_logic.BookingError:
                        pass
                    t += dur + RND.choice([0, 0, 30, 60])
    # a few cancelled / no-shows in the past for reports
    for _ in range(55):
        d = today - timedelta(days=RND.randint(1, 120))
        br = RND.choice([tdw, skn])
        emps = appt_logic.bookable_employees(br.id, d)
        if not emps:
            continue
        s = [svc_by_name["Men's Haircut"]]
        start = datetime(d.year, d.month, d.day, RND.choice([11, 13, 15, 17]), 0)
        try:
            a = appt_logic.create_appointment(br.id, RND.choice(clients), start, [x.id for x in s], RND.choice(emps).id, source=RND.choice(["online", "phone", "instagram"]), override_conflict=True)
            a.status = RND.choice(["cancelled", "cancelled", "no_show"]); a.cancel_reason = "Client unavailable" if a.status == "cancelled" else "Did not arrive"
        except appt_logic.BookingError:
            pass
    # RK's blocked slot example
    db.session.add(BlockedSlot(branch_id=tdw.id, employee_id=L["rk"].id, start_dt=datetime.combine(today + timedelta(days=2), datetime.min.time()) + timedelta(hours=14),
                               end_dt=datetime.combine(today + timedelta(days=2), datetime.min.time()) + timedelta(hours=16), reason="Supplier meeting"))
    # a WhatsApp outbox sample
    from .logic import whatsapp as wa
    for a in Appointment.query.filter(Appointment.start_dt >= now_).order_by(Appointment.start_dt).limit(6):
        wa.appointment_confirmed(a)


def reset_demo():
    from .logic.web import ensure_website_content
    wipe()
    seed(history=True)
    ensure_website_content()


def reset_clean():
    from .logic.web import ensure_website_content
    wipe()
    seed(history=False, mode="clean")
    ensure_website_content()
