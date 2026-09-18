# RK Hair Pro – Central Salon Management App

Black-and-gold, Open Sans, built in **Python (Flask + SQLite)**. One central app for every branch:
finance, appointments, billing/POS, HR & payroll, inventory, operations, marketing and reports –
plus a public website with real-time online booking and an employee self-service portal.

## Run it

```bash
pip install -r requirements.txt
python run.py            # or double-click "Start RK Hair Pro.bat"
```

Open **http://127.0.0.1:5000** (public website) · **http://127.0.0.1:5000/login** (staff).
The first start creates the demo data (about 1–2 minutes). To re-create it relative to *today*:
**Settings → Demo data → Reset**. To wipe everything for go-live: **Settings → Demo data → Start fresh**.

## Demo logins (password for all: `Gold@123`)

| Who | Mobile (user ID) | Sees |
|---|---|---|
| Rakesh Kumar (RK) – Super Admin | 9820000001 | everything, all branches |
| Sanjay Pawar – Branch Admin, Thakurdwar | 9820000011 | Thakurdwar only |
| Vikas Naik – Branch Admin, Sikka Nagar | 9820000021 | Sikka Nagar only |
| Neha Shah – Reception, Thakurdwar | 9820000012 | appointments + billing |
| Imran Sheikh – Senior Stylist | 9820000013 | employee portal |
| Rohit Jadhav – Assistant | 9820000015 | employee portal |

(All names, numbers, prices and mobile numbers are **sample data**. The one-click demo buttons are on the login page.)

## What is where

| Area | Screens |
|---|---|
| Public website | `/` home, `/book` real-time booking (branch, stylist, time drop-downs), `/my-booking`, `/feedback/<token>` |
| Appointments | day calendar per stylist, list view, new / modify / cancel, block time, RK's branch for a chosen day, services & prices |
| Billing / POS | services + products, coupon, tips per employee, split payment (cash / UPI account / card), part-payment, void |
| Finance | summary (day / MTD / YTD / custom), ledger, accounts (unlimited cash/UPI/bank/card), tips, day close, balance sheet, income & expense heads, vendors |
| HR | employees + KYC, hierarchy, permissions, attendance sheet, face-attendance kiosk, leaves, payroll, reimbursements, announcements |
| Inventory | stock per branch, receive / use / wastage / count, reserve-limit alerts, stock requests, vendors, item master |
| Operations | daily checklists, tasks, equipment & maintenance, holidays, audit trail |
| Marketing | campaigns, coupons, lead tracker, feedback & reviews, booking links, birthdays |
| Reports | 42 reports in 7 departments – all with Today / MTD / YTD / custom range, CSV export, print |
| Employee portal | attendance, earnings & tips, payslips, leave, reimbursements, announcements, performance |

## Public website (client interface)

Home page sections: hero, Meet RK, **transformation video slider**, services by category, **client testimonial slider**, stylists, branches (call / directions / book), follow-us band, plus a floating **Call** and **WhatsApp** button on every page.

* **Call button** always opens a chooser first: the central bookings number or any branch (tap to dial on a phone).
* **Hero logo animation:** the home page plays RK's logo animation (`rkhp/static/media/rk_logo_animation.mp4`), blended into the black hero, muted, once, then holds on the logo, with an optional "Play with sound" button. Visitors on data-saver / slow networks / reduced-motion see the static logo instead.
* **Website Studio** (staff login → *Website Studio*, Super Admin only unless the *Website* permission is granted to someone):
  * *Page content* – headline, intro, Meet RK text / points / photo, every section heading, closing section, and show/hide for each section.
  * *Logo animation* – upload a new one, loop on/off, sound button on/off, restore the original, or fall back to the static logo.
  * *Video slider* and *Testimonials* – add, edit, reorder, hide, delete. Upload .mp4 / .webm / .mov, or paste a YouTube / .mp4 link. Sample tiles can be removed with one click.
* **Phone numbers & social links:** Settings → *Contact & social* (central number, WhatsApp, Instagram, Facebook, YouTube, email). Branch numbers: Settings → Branches.
* The numbers and links that ship are **sample placeholders** – replace them with RK Hair Pro's real ones.

## Business rules built in (all editable in Settings)

* Financial year 1 April – 31 March; **YTD** = financial-year-to-date.
* Salary ₹18,000, per-day = salary ÷ 30. Deductions: absent 1 day, half-day ½, unpaid leave 1, every 3 late marks ½, every 3 early exits ½ (15-minute grace). 1 paid leave/month. Weekly off is paid and set per employee.
* Incentive = 10% of the employee's **service** business (before GST, excluding tips and retail).
* Tips belong to the employee: paid at end of day **or** accumulated and paid with salary (per employee); full or partial payouts; tracked as a liability, never as income.
* GST is a switch (off by default); when on, CGST + SGST is a liability, not income.
* RK works from Thakurdwar by default and can choose another branch for any specific day.

## WhatsApp & face recognition (demo mode)

* Every WhatsApp message (booking confirmed / changed / cancelled, 1-hour reminder, payment thank-you, tip payout, salary, leave/stock/role decisions, low-stock alert) is generated and shown in **WhatsApp Outbox**.
  Going live = set a provider in Settings → WhatsApp and implement `_deliver()` in `rkhp/logic/whatsapp.py` (single function).
* The reminder job runs in a background thread every minute.
* The attendance kiosk opens the camera, captures a photo and marks date & time; the face *match* is simulated (choose who is scanned). Replace the matching step in `rkhp/routes/hr.py → kiosk_scan` with a face-recognition engine.

## Project layout

```
run.py                     start the server
rkhp/__init__.py           app factory, navigation, scheduler
rkhp/models.py             database models
rkhp/logic/                finance, billing, appointments, payroll, inventory, whatsapp, reports engines
rkhp/routes/               one blueprint per module
rkhp/templates, static/    black & gold UI (Open Sans)
rkhp/seed.py               demo data generator
instance/rkhp.db           SQLite database (delete to start over)
```
