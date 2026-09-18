"""Database models for RK Hair Pro - central multi-branch salon management."""
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def now():
    return datetime.now()


# ----------------------------------------------------------------------------
# Roles & permissions
# ----------------------------------------------------------------------------
ROLES = [
    ("super_admin", "Super Admin"),
    ("branch_admin", "Branch Admin"),
    ("senior_stylist", "Senior Stylist"),
    ("stylist", "Stylist"),
    ("assistant", "Assistant"),
    ("reception", "Reception"),
]
ROLE_LABEL = dict(ROLES)
ROLE_RANK = {r: i for i, (r, _) in enumerate(ROLES)}

PERMS = [
    ("appointments", "Appointments (view & manage bookings)"),
    ("billing", "Billing & payment collection"),
    ("finance", "Finance (ledger, accounts, tips, day close)"),
    ("inventory", "Inventory (stock in / out)"),
    ("hr", "HR (employees, attendance, payroll)"),
    ("reports", "Reports"),
    ("marketing", "Marketing & clients"),
    ("operations", "Operations (checklists, tasks, equipment)"),
    ("settings", "Company settings"),
    ("website", "Website (edit the public site: animation, videos, reviews, content)"),
]
ALL_PERMS = [p for p, _ in PERMS]
DEFAULT_PERMS = {
    "super_admin": ALL_PERMS,
    "branch_admin": [p for p in ALL_PERMS if p not in ("settings", "website")],
    "reception": ["appointments", "billing"],
    "senior_stylist": [],
    "stylist": [],
    "assistant": [],
}

ACTIVE_APPT = ("booked", "confirmed", "arrived", "in_service", "completed")
APPT_STATUS = [
    ("booked", "Booked"), ("confirmed", "Confirmed"), ("arrived", "Arrived"),
    ("in_service", "In service"), ("completed", "Completed"),
    ("cancelled", "Cancelled"), ("no_show", "No-show"),
]


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)


class Branch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    code = db.Column(db.String(8), nullable=False)
    address = db.Column(db.String(240))
    phone = db.Column(db.String(20))
    open_time = db.Column(db.String(5), default="10:00")
    close_time = db.Column(db.String(5), default="21:00")
    slot_minutes = db.Column(db.Integer, default=30)
    monthly_target = db.Column(db.Float, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)


class Employee(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(15), unique=True, nullable=False)   # login id
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), default="stylist")
    designation = db.Column(db.String(80))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))       # access scope (None = all branches)
    home_branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))  # where they normally work
    reports_to_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    active = db.Column(db.Boolean, default=True)
    login_blocked = db.Column(db.Boolean, default=False)
    perms = db.Column(db.String(200))            # comma list; None => role defaults
    takes_appointments = db.Column(db.Boolean, default=False)
    public_visible = db.Column(db.Boolean, default=True)   # shown on online booking page
    bio = db.Column(db.String(240))
    salary = db.Column(db.Float, default=18000)
    commission_pct = db.Column(db.Float, default=10)
    monthly_target = db.Column(db.Float, default=0)
    weekly_off = db.Column(db.Integer, default=0)          # Monday=0 .. Sunday=6
    shift_start = db.Column(db.String(5), default="10:00")
    shift_end = db.Column(db.String(5), default="21:00")
    tip_mode = db.Column(db.String(10), default="daily")   # daily | salary
    joined_on = db.Column(db.Date, default=date.today)
    dob = db.Column(db.Date)
    email = db.Column(db.String(120))
    address = db.Column(db.String(240))
    aadhaar = db.Column(db.String(20))
    pan = db.Column(db.String(12))
    emergency_name = db.Column(db.String(80))
    emergency_phone = db.Column(db.String(15))
    bank_name = db.Column(db.String(80))
    bank_account = db.Column(db.String(30))
    ifsc = db.Column(db.String(15))
    upi_id = db.Column(db.String(60))
    photo = db.Column(db.String(200))
    face_photo = db.Column(db.String(200))
    aadhaar_doc = db.Column(db.String(200))
    pan_doc = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=now)

    branch = db.relationship("Branch", foreign_keys=[branch_id])
    home_branch = db.relationship("Branch", foreign_keys=[home_branch_id])
    manager = db.relationship("Employee", remote_side=[id], foreign_keys=[reports_to_id])

    # ---- auth ----
    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, pw)

    @property
    def is_active(self):       # Flask-Login
        return bool(self.active) and not self.login_blocked

    # ---- role/permissions ----
    @property
    def is_super(self):
        return self.role == "super_admin"

    @property
    def role_label(self):
        return ROLE_LABEL.get(self.role, self.role)

    @property
    def perm_set(self):
        if self.is_super:
            return set(ALL_PERMS)
        if self.perms is None:
            return set(DEFAULT_PERMS.get(self.role, []))
        return {p for p in self.perms.split(",") if p}

    def has_perm(self, p):
        return p in self.perm_set

    @property
    def is_manager(self):
        return self.role in ("super_admin", "branch_admin")

    @property
    def initials(self):
        parts = self.name.split()
        return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()

    @property
    def per_day(self):
        return round((self.salary or 0) / 30.0, 2)

    def location_on(self, d):
        """Branch id where this employee works on date d (day override -> home branch)."""
        ov = EmployeeDayBranch.query.filter_by(employee_id=self.id, date=d).first()
        if ov:
            return ov.branch_id           # None means not working at any branch that day
        return self.home_branch_id


class EmployeeDayBranch(db.Model):
    """Lets an employee (e.g. RK) work from a different branch on a particular day."""
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    note = db.Column(db.String(120))
    __table_args__ = (db.UniqueConstraint("employee_id", "date"),)
    branch = db.relationship("Branch")
    employee = db.relationship("Employee")


class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.String(100))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))   # None = all branches
    branch = db.relationship("Branch")


# ----------------------------------------------------------------------------
# Clients, services, appointments
# ----------------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(15), unique=True, nullable=False)
    email = db.Column(db.String(120))
    gender = db.Column(db.String(10))
    dob = db.Column(db.Date)
    anniversary = db.Column(db.Date)
    source = db.Column(db.String(30))            # Walk-in / Instagram / Website / Phone / Referral / Google
    notes = db.Column(db.String(400))
    loyalty_points = db.Column(db.Integer, default=0)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    whatsapp_optin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)
    branch = db.relationship("Branch")


class ServiceCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    sort = db.Column(db.Integer, default=0)


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("service_category.id"))
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10), default="Unisex")     # Men / Women / Unisex
    price = db.Column(db.Float, default=0)
    duration = db.Column(db.Integer, default=30)             # minutes
    description = db.Column(db.String(240))
    active = db.Column(db.Boolean, default=True)
    category = db.relationship("ServiceCategory")


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    start_dt = db.Column(db.DateTime, nullable=False)
    end_dt = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(15), default="booked")
    source = db.Column(db.String(20), default="offline")   # online / offline / instagram / website / phone / walk-in
    notes = db.Column(db.String(400))
    cancel_reason = db.Column(db.String(200))
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    created_at = db.Column(db.DateTime, default=now)
    reminder_sent = db.Column(db.Boolean, default=False)
    invoice_id = db.Column(db.Integer)

    branch = db.relationship("Branch")
    client = db.relationship("Client")
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    created_by = db.relationship("Employee", foreign_keys=[created_by_id])
    lines = db.relationship("AppointmentService", cascade="all, delete-orphan", backref="appointment")

    @property
    def total(self):
        return sum(l.price for l in self.lines)

    @property
    def duration(self):
        return int((self.end_dt - self.start_dt).total_seconds() // 60)

    @property
    def service_names(self):
        return ", ".join(l.service.name for l in self.lines if l.service)

    @property
    def is_active(self):
        return self.status in ACTIVE_APPT


class AppointmentService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False)
    price = db.Column(db.Float, default=0)
    duration = db.Column(db.Integer, default=30)
    service = db.relationship("Service")


class BlockedSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    start_dt = db.Column(db.DateTime, nullable=False)
    end_dt = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(120))
    employee = db.relationship("Employee")
    branch = db.relationship("Branch")


# ----------------------------------------------------------------------------
# Billing
# ----------------------------------------------------------------------------
class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"))
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    subtotal = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    coupon_code = db.Column(db.String(30))
    gst_amount = db.Column(db.Float, default=0)
    tip_total = db.Column(db.Float, default=0)
    grand_total = db.Column(db.Float, default=0)      # services + products - discount + gst + tips
    paid_amount = db.Column(db.Float, default=0)
    status = db.Column(db.String(10), default="paid")  # paid / partial / void
    notes = db.Column(db.String(300))
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))

    branch = db.relationship("Branch")
    client = db.relationship("Client")
    created_by = db.relationship("Employee")
    items = db.relationship("InvoiceItem", cascade="all, delete-orphan", backref="invoice")
    tips = db.relationship("InvoiceTip", cascade="all, delete-orphan", backref="invoice")

    @property
    def due(self):
        return round((self.grand_total or 0) - (self.paid_amount or 0), 2)

    @property
    def net_business(self):
        return round((self.subtotal or 0) - (self.discount or 0), 2)


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    kind = db.Column(db.String(10), default="service")   # service / product
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"))
    description = db.Column(db.String(120))
    qty = db.Column(db.Float, default=1)
    unit_price = db.Column(db.Float, default=0)
    amount = db.Column(db.Float, default=0)
    net_amount = db.Column(db.Float, default=0)          # after discount share, before GST
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    employee = db.relationship("Employee")
    service = db.relationship("Service")


class InvoiceTip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    amount = db.Column(db.Float, default=0)
    employee = db.relationship("Employee")


# ----------------------------------------------------------------------------
# Finance
# ----------------------------------------------------------------------------
ACCOUNT_TYPES = [("cash", "Cash counter"), ("upi", "UPI account"), ("bank", "Bank account"), ("card", "Card settlement")]
# nature drives how a head is treated in P&L / balance sheet
NATURES = [
    ("revenue", "Business income"), ("expense", "Business expense"),
    ("tip_in", "Tips received (pass-through)"), ("tip_out", "Tips paid out"),
    ("gst_in", "GST collected"), ("gst_out", "GST remitted"),
    ("vendor_pay", "Vendor payment (stock bills)"),
    ("equity_in", "Owner capital introduced"), ("equity_out", "Owner drawings"),
]


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))   # None = company level
    opening_balance = db.Column(db.Float, default=0)
    upi_id = db.Column(db.String(60))
    bank_name = db.Column(db.String(80))
    notes = db.Column(db.String(200))
    active = db.Column(db.Boolean, default=True)
    branch = db.relationship("Branch")

    @property
    def type_label(self):
        return dict(ACCOUNT_TYPES).get(self.type, self.type)


class Category(db.Model):
    """Income / expense heads."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(10), nullable=False)      # income / expense
    nature = db.Column(db.String(12), nullable=False)
    group = db.Column(db.String(60))
    system = db.Column(db.Boolean, default=False)         # used by the app automatically; cannot be deleted
    active = db.Column(db.Boolean, default=True)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now)
    kind = db.Column(db.String(10), nullable=False)      # in / out / transfer
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"))
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    to_account_id = db.Column(db.Integer, db.ForeignKey("account.id"))   # transfers
    amount = db.Column(db.Float, nullable=False)
    mode = db.Column(db.String(12))                      # cash / upi / card / cheque / bank
    party = db.Column(db.String(120))
    reference = db.Column(db.String(60))
    notes = db.Column(db.String(300))
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))
    payroll_id = db.Column(db.Integer)
    batch = db.Column(db.String(40), index=True)
    petty = db.Column(db.Boolean, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))

    branch = db.relationship("Branch")
    category = db.relationship("Category")
    account = db.relationship("Account", foreign_keys=[account_id])
    to_account = db.relationship("Account", foreign_keys=[to_account_id])
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    vendor = db.relationship("Vendor")
    invoice = db.relationship("Invoice")
    created_by = db.relationship("Employee", foreign_keys=[created_by_id])


class DayClose(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    expected_cash = db.Column(db.Float, default=0)
    counted_cash = db.Column(db.Float, default=0)
    difference = db.Column(db.Float, default=0)
    note = db.Column(db.String(300))
    closed_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    closed_at = db.Column(db.DateTime, default=now)
    branch = db.relationship("Branch")
    closed_by = db.relationship("Employee")
    __table_args__ = (db.UniqueConstraint("branch_id", "date"),)


# ----------------------------------------------------------------------------
# Inventory
# ----------------------------------------------------------------------------
class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(60))
    contact = db.Column(db.String(80))
    mobile = db.Column(db.String(15))
    gstin = db.Column(db.String(20))
    address = db.Column(db.String(200))
    terms = db.Column(db.String(80))
    active = db.Column(db.Boolean, default=True)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    sku = db.Column(db.String(30))
    category = db.Column(db.String(50))
    unit = db.Column(db.String(10), default="pcs")
    cost = db.Column(db.Float, default=0)
    sell_price = db.Column(db.Float, default=0)
    is_retail = db.Column(db.Boolean, default=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"))
    active = db.Column(db.Boolean, default=True)
    vendor = db.relationship("Vendor")


class StockLevel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    qty = db.Column(db.Float, default=0)
    reorder_level = db.Column(db.Float, default=0)
    item = db.relationship("Item")
    branch = db.relationship("Branch")
    __table_args__ = (db.UniqueConstraint("item_id", "branch_id"),)

    @property
    def low(self):
        return self.qty <= (self.reorder_level or 0)


MOVE_KINDS = [
    ("opening", "Opening stock"), ("purchase", "Purchase / stock in"), ("consumption", "Used in service"),
    ("sale", "Retail sale"), ("wastage", "Wastage / damage"), ("adjustment", "Stock adjustment"),
    ("transfer_in", "Transfer in"), ("transfer_out", "Transfer out"), ("return", "Return to vendor"),
]


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    kind = db.Column(db.String(15), nullable=False)
    qty = db.Column(db.Float, nullable=False)              # signed (+ in, - out)
    unit_cost = db.Column(db.Float, default=0)
    date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"))
    bill_id = db.Column(db.Integer, db.ForeignKey("purchase_bill.id"))
    note = db.Column(db.String(200))
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    item = db.relationship("Item")
    branch = db.relationship("Branch")
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    created_by = db.relationship("Employee", foreign_keys=[created_by_id])


class PurchaseBill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    bill_no = db.Column(db.String(40))
    date = db.Column(db.Date, nullable=False)
    total = db.Column(db.Float, default=0)
    paid = db.Column(db.Float, default=0)
    note = db.Column(db.String(200))
    vendor = db.relationship("Vendor")
    branch = db.relationship("Branch")
    moves = db.relationship("StockMovement", backref="bill")

    @property
    def due(self):
        return round((self.total or 0) - (self.paid or 0), 2)


class StockRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    qty = db.Column(db.Float, nullable=False)
    urgency = db.Column(db.String(10), default="normal")
    note = db.Column(db.String(200))
    status = db.Column(db.String(12), default="pending")    # pending / approved / rejected / received
    requested_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    decision_note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=now)
    decided_at = db.Column(db.DateTime)
    item = db.relationship("Item")
    branch = db.relationship("Branch")
    requested_by = db.relationship("Employee", foreign_keys=[requested_by_id])
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])


class ServiceConsumable(db.Model):
    """Products auto-deducted from stock every time the service is billed."""
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    qty = db.Column(db.Float, default=1)
    service = db.relationship("Service")
    item = db.relationship("Item")


# ----------------------------------------------------------------------------
# HRMS
# ----------------------------------------------------------------------------
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    check_in = db.Column(db.DateTime)
    check_out = db.Column(db.DateTime)
    status = db.Column(db.String(10), default="present")   # present / half_day / absent / leave
    late_min = db.Column(db.Integer, default=0)
    early_min = db.Column(db.Integer, default=0)
    worked_min = db.Column(db.Integer, default=0)
    method = db.Column(db.String(10), default="face")      # face / manual
    snapshot = db.Column(db.String(200))
    note = db.Column(db.String(200))
    employee = db.relationship("Employee")
    __table_args__ = (db.UniqueConstraint("employee_id", "date"),)


class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    from_date = db.Column(db.Date, nullable=False)
    to_date = db.Column(db.Date, nullable=False)
    kind = db.Column(db.String(10), default="paid")        # paid / unpaid / sick
    reason = db.Column(db.String(200))
    status = db.Column(db.String(10), default="pending")
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    decided_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])

    @property
    def days(self):
        return (self.to_date - self.from_date).days + 1


class RoleRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    from_role = db.Column(db.String(20))
    to_role = db.Column(db.String(20))
    reason = db.Column(db.String(240))
    status = db.Column(db.String(10), default="pending")
    requested_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    created_at = db.Column(db.DateTime, default=now)
    decided_at = db.Column(db.DateTime)
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    requested_by = db.relationship("Employee", foreign_keys=[requested_by_id])
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])


class Reimbursement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    date = db.Column(db.Date, default=date.today)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"))
    receipt = db.Column(db.String(200))
    settle_mode = db.Column(db.String(10), default="salary")   # daily / salary
    status = db.Column(db.String(10), default="pending")      # pending / approved / rejected / paid
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    paid_on = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=now)
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    branch = db.relationship("Branch")
    category = db.relationship("Category")
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])


class Payroll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    month = db.Column(db.String(7), nullable=False)         # YYYY-MM
    base_salary = db.Column(db.Float, default=0)
    per_day = db.Column(db.Float, default=0)
    present_days = db.Column(db.Float, default=0)
    absent_days = db.Column(db.Float, default=0)
    half_days = db.Column(db.Float, default=0)
    late_marks = db.Column(db.Integer, default=0)
    early_exits = db.Column(db.Integer, default=0)
    unpaid_leave_days = db.Column(db.Float, default=0)
    paid_leave_days = db.Column(db.Float, default=0)
    week_offs = db.Column(db.Integer, default=0)
    deduction_days = db.Column(db.Float, default=0)
    deduction_amt = db.Column(db.Float, default=0)
    service_revenue = db.Column(db.Float, default=0)
    incentive_pct = db.Column(db.Float, default=10)
    incentive_amt = db.Column(db.Float, default=0)
    reimb_amt = db.Column(db.Float, default=0)
    tips_amt = db.Column(db.Float, default=0)
    net_salary = db.Column(db.Float, default=0)             # base - deductions + incentive + reimbursements
    total_payable = db.Column(db.Float, default=0)          # net_salary + tips accumulated
    status = db.Column(db.String(10), default="draft")      # draft / paid
    paid_on = db.Column(db.Date)
    mode = db.Column(db.String(10))
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"))
    employee = db.relationship("Employee")
    branch = db.relationship("Branch")
    account = db.relationship("Account")
    __table_args__ = (db.UniqueConstraint("employee_id", "month"),)


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))    # None = everyone
    pinned = db.Column(db.Boolean, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    created_at = db.Column(db.DateTime, default=now)
    branch = db.relationship("Branch")
    created_by = db.relationship("Employee")


# ----------------------------------------------------------------------------
# Communication / system
# ----------------------------------------------------------------------------
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    title = db.Column(db.String(120))
    body = db.Column(db.String(300))
    link = db.Column(db.String(200))
    kind = db.Column(db.String(20), default="info")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)


class WhatsAppMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to_name = db.Column(db.String(100))
    to_mobile = db.Column(db.String(15))
    kind = db.Column(db.String(30))
    body = db.Column(db.Text)
    status = db.Column(db.String(12), default="simulated")   # simulated / sent / failed
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    created_at = db.Column(db.DateTime, default=now)
    branch = db.relationship("Branch")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    action = db.Column(db.String(40))
    entity = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    detail = db.Column(db.String(300))
    branch_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=now)
    user = db.relationship("Employee")


# ----------------------------------------------------------------------------
# Marketing
# ----------------------------------------------------------------------------
class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(150))
    kind = db.Column(db.String(8), default="percent")        # percent / flat
    value = db.Column(db.Float, default=10)
    min_bill = db.Column(db.Float, default=0)
    max_discount = db.Column(db.Float, default=0)
    valid_from = db.Column(db.Date)
    valid_to = db.Column(db.Date)
    usage_limit = db.Column(db.Integer, default=0)           # 0 = unlimited
    used_count = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)


class Campaign(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    segment = db.Column(db.String(30), default="all")
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    message = db.Column(db.Text)
    status = db.Column(db.String(10), default="draft")
    sent_count = db.Column(db.Integer, default=0)
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    branch = db.relationship("Branch")


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(15))
    source = db.Column(db.String(30))
    interest = db.Column(db.String(120))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    status = db.Column(db.String(12), default="new")         # new / contacted / booked / lost
    follow_up = db.Column(db.Date)
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now)
    branch = db.relationship("Branch")


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(24), unique=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    rating = db.Column(db.Integer)
    comment = db.Column(db.String(400))
    status = db.Column(db.String(10), default="requested")   # requested / received / resolved
    follow_up_note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now)
    submitted_at = db.Column(db.DateTime)
    client = db.relationship("Client")
    branch = db.relationship("Branch")
    employee = db.relationship("Employee")


class Testimonial(db.Model):
    """Client videos and testimonials shown on the public website sliders."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(8), default="video")          # video / text
    tag = db.Column(db.String(30))                            # Haircut / Haircolour / Styling / Grooming ...
    title = db.Column(db.String(120))
    client_name = db.Column(db.String(80))
    quote = db.Column(db.String(500))
    rating = db.Column(db.Integer, default=5)
    video_file = db.Column(db.String(200))                    # uploaded file (uploads/web)
    video_url = db.Column(db.String(300))                     # YouTube link / direct mp4 link / Instagram reel link
    poster_file = db.Column(db.String(200))
    sort = db.Column(db.Integer, default=100)
    active = db.Column(db.Boolean, default=True)
    is_sample = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)

    @property
    def yt_id(self):
        import re
        m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/))([\w-]{11})", self.video_url or "")
        return m.group(1) if m else None

    @property
    def direct_url(self):
        u = (self.video_url or "").lower().split("?")[0]
        return self.video_url if u.endswith((".mp4", ".webm", ".mov", ".m4v")) else None


# ----------------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------------
class ChecklistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))     # None = all branches
    shift = db.Column(db.String(10), default="opening")               # opening / closing / hygiene
    title = db.Column(db.String(150), nullable=False)
    active = db.Column(db.Boolean, default=True)


class ChecklistEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("checklist_item.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    done_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    done_at = db.Column(db.DateTime, default=now)
    remark = db.Column(db.String(150))
    item = db.relationship("ChecklistItem")
    done_by = db.relationship("Employee")
    __table_args__ = (db.UniqueConstraint("item_id", "branch_id", "date"),)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    detail = db.Column(db.String(300))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"))
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(8), default="normal")
    status = db.Column(db.String(10), default="open")        # open / doing / done
    created_at = db.Column(db.DateTime, default=now)
    completed_at = db.Column(db.DateTime)
    branch = db.relationship("Branch")
    assigned_to = db.relationship("Employee", foreign_keys=[assigned_to_id])
    created_by = db.relationship("Employee", foreign_keys=[created_by_id])


class Equipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    purchase_date = db.Column(db.Date)
    cost = db.Column(db.Float, default=0)
    warranty_till = db.Column(db.Date)
    last_service = db.Column(db.Date)
    next_service = db.Column(db.Date)
    status = db.Column(db.String(12), default="working")     # working / repair / retired
    notes = db.Column(db.String(200))
    branch = db.relationship("Branch")
