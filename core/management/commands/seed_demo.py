"""Populate the database with the demo data the static templates hardcode.

The volume here is deliberate: it reproduces the *summary strips* on the
mockups, not just the handful of rows that happen to be visible. Every count a
chip or a strip claims has to be true when you filter the real table.

Idempotent: safe to re-run. ``--reset`` wipes the business tables first; without
it the command is a no-op once the data exists.

    python manage.py seed_demo --reset

Anchors
-------
TODAY is pinned at 10 Aug 2026 and *every* relative date derives from it, so
"3d pending", "due in 5 days" and "received 12 days ago" all agree.

Numbering
---------
Two mechanisms, used deliberately:

* Hero numbers the client has already been shown (QTN-0401/0402/0403/0405/0416
  /0417/0418/0419/0421/0422) are passed to ``objects.create(number=...)``
  directly — they are historical facts, not something a counter should invent.
* Everything else is minted by ``Sequence.next_number``. The QTN sequence is
  pre-seeded at 422 and the SO sequence at 1030, so filler quotations start at
  QTN-0423 (never colliding with a hero) and sales orders run SO-1031 upwards.
  The order specs are listed in SO order, which is what puts SO-1039..SO-1044
  on the right jobs.

``Quotation.create_revision()`` mints — and then discards — a number, so the
QTN sequence is rewound by one after each revision to keep the run gapless.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from accounts.models import SalesOrder, SOItem
from core.constants import (
    ApprovalSource, Health, ItemCodeApproval, OrderStatus, OrderType, Priority,
    QuotationStatus, QueryStatus, SheetStatus, StationStatus,
)
from core.models import AuditLog, Notification, Sequence
from design.models import DesignQuery, DrawingRevision, MaterialReservation, QueryMessage
from masters.models import (
    Customer, Finish, Grade, ItemCode, ItemCodeRate, Material, ProcessStation,
    Rack, ReorderLevel, SheetSize, Supplier, Thickness,
)
from production.models import JobCard, JobCardItem, JobCardStation
from sales.models import PriceTier, Quotation, QuotationItem
from stores.models import DispatchRecord, Sheet

User = get_user_model()

# --------------------------------------------------------------------------
# THE single anchor. Nothing below may call date.today().
# --------------------------------------------------------------------------
TODAY = date(2026, 8, 10)


def day(offset):
    """A date `offset` days after TODAY (negative = in the past)."""
    return TODAY + timedelta(days=offset)


def ago(days_before):
    return TODAY - timedelta(days=days_before)


def at(on_date, hour=10, minute=30):
    """Timezone-aware datetime on `on_date`."""
    naive = datetime.combine(on_date, time(hour, minute))
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------
CUSTOMERS = [
    # key, name, address, city, state, pincode, gstin, contact, phone, email, credit
    ('VF', 'Veeransh Fabricators', 'Plot 42, MIDC Satpur, Nashik 422007', 'Nashik',
     'Maharashtra', '422007', '27AABCV1234F1Z5', 'Mr. Rajesh Kulkarni',
     '+91 98765 43210', 'rajesh@veeransh.com', 500000),
    ('BS', 'Bharat Switchgear Co.', 'Unit 14-B, Marol Industrial Estate, Mumbai 400059',
     'Mumbai', 'Maharashtra', '400059', '27AABCB5678K1Z3', 'Mr. Rajesh Kapoor',
     '+91 98200 11223', 'rajesh@bharatswitchgear.com', 800000),
    ('MT', 'Marigold Textiles', 'Survey 88, Ichalkaranji Road, Kolhapur 416115',
     'Kolhapur', 'Maharashtra', '416115', '27AACFM9012L1Z7', 'Ms. Sunita Deshmukh',
     '+91 99870 55418', 'sunita@marigoldtex.in', 250000),
    ('TP', 'Trident Pumps Ltd', 'Plot E-7, MIDC Chakan, Pune 410501', 'Pune',
     'Maharashtra', '410501', '27AAECT3456M1Z9', 'Mr. Anil Joshi',
     '+91 98220 77340', 'anil.joshi@tridentpumps.com', 600000),
    ('OC', 'Orion Compressors', 'Gat 210, Shirwal MIDC, Satara 412801', 'Satara',
     'Maharashtra', '412801', '27AADCO7890N1Z2', 'Mr. Farid Shaikh',
     '+91 97650 21188', 'farid@orioncomp.co.in', 300000),
]

# name -> cell/group, so the operator roster can group the 12 stations.
STATIONS = [
    ('Material Cutting / Shearing', 'Cutting/Polishing'),
    ('Surface Cleaning / Degreasing', 'Coating/Expose'),
    ('Photoresist Coating + Exposure + Developing', 'Coating/Expose'),
    ('Chemical Etching', 'Etching/Stripping'),
    ('Stripping / Neutralising', 'Etching/Stripping'),
    ('Colour Filling', 'Fill/Cure/Pack'),
    ('Curing / Baking', 'Fill/Cure/Pack'),
    ('Surface Polishing', 'Cutting/Polishing'),
    ('Lamination', 'Fill/Cure/Pack'),
    ('Trimming', 'Trim/Drill'),
    ('Hole Drilling', 'Trim/Drill'),
    ('Packing', 'Fill/Cure/Pack'),
]

# The station the Floor Manager unticks on every card in this demo.
SKIPPED_STATION = 'Lamination'

# --------------------------------------------------------------------------
# Item codes. approved_on is deliberately EARLIER than every order that uses
# the code — a rate cannot be locked in after the order that quoted it.
# --------------------------------------------------------------------------
ITEM_CODES = [
    # code, cust, description, material, thickness, size, finish,
    #   rate, approved_via, approved_on, is_active
    ('VF-NP-150', 'VF', 'Rating Plate 150x110', 'SS 304', '0.5mm', '150 x 110',
     'Black Epoxy Filled', '380.00', 'QTN-0398', date(2026, 6, 12), True),
    ('VF-DP-080', 'VF', 'Data Plate 80x50', 'Aluminium', '1.0mm', '80 x 50',
     'Anodized', '175.00', 'QTN-0402', date(2026, 6, 28), True),
    ('VF-TAG-040', 'VF', 'Valve Tag 40x25', 'Brass', '1.5mm', '40 x 25',
     'Natural Finish', '96.00', 'QTN-0361', date(2026, 3, 3), False),
    ('BS-PNL-200', 'BS', 'Panel Plate 200x150', 'SS 304', '2.0mm', '200 x 150',
     'Black Epoxy Filled', '1382.00', 'QTN-0416', date(2026, 8, 3), True),
    ('BS-RAT-100', 'BS', 'Rating Label 100x60', 'Aluminium', '1.0mm', '100 x 60',
     'Anodized', '432.00', 'QTN-0416', date(2026, 8, 3), True),
    ('MT-LBL-060', 'MT', 'Machine Label 60x40', 'SS 316', '0.5mm', '60 x 40',
     'Laminated, Clear Coat', '298.00', 'QTN-0389', date(2026, 5, 22), True),
    ('TP-NP-120', 'TP', 'Pump Nameplate 120x80', 'SS 304', '1.5mm', '120 x 80',
     'Black Epoxy Filled', '512.00', 'QTN-0405', date(2026, 7, 14), True),
    ('TP-SER-050', 'TP', 'Serial Tag 50x30', 'Brass', '1.0mm', '50 x 30',
     'Natural Finish', '186.00', 'QTN-0405', date(2026, 7, 14), True),
    # Orion used to be an empty customer on the mockups; these two give it a
    # catalogue. Approved on the same (pre-system) quotation, like QTN-0398.
    ('OC-NP-110', 'OC', 'Compressor Nameplate 110x70', 'SS 304', '1.5mm', '110 x 70',
     'Black Epoxy Filled', '445.00', 'QTN-0394', date(2026, 6, 5), True),
    ('OC-TAG-035', 'OC', 'Oil Level Tag 35x20', 'Brass', '1.5mm', '35 x 20',
     'Natural Finish', '78.00', 'QTN-0394', date(2026, 6, 5), True),
    # Typed on a live quotation, never approved -> no locked rate.
    ('BS-TAG-075', 'BS', 'Valve Tag 75x40', 'Brass', '1.5mm', '75 x 40',
     'Natural Finish', None, '', None, True),
    ('MT-NP-090', 'MT', 'Nameplate 90x60', 'SS 304', '1.0mm', '90 x 60',
     'Anodized', None, '', None, True),
    # --- the Bharat Switchgear catalogue the 10-line showcase order draws on.
    # All locked in on one pre-system quotation, well before that order.
    ('BS-NP-160', 'BS', 'Nameplate 160x100', 'SS 304', '1.5mm', '160 x 100',
     'Black Epoxy Filled', '640.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-DP-090', 'BS', 'Data Plate 90x60', 'Aluminium', '1.0mm', '90 x 60',
     'Anodized', '268.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-LBL-120', 'BS', 'Legend Plate 120x80', 'SS 316', '0.5mm', '120 x 80',
     'Laminated, Clear Coat', '355.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-TAG-055', 'BS', 'Cable Tag 55x30', 'Brass', '1.0mm', '55 x 30',
     'Natural Finish', '122.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-SGN-250', 'BS', 'Warning Sign 250x180', 'Aluminium', '0.5mm', '250 x 180',
     'Anodized', '512.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-BRK-140', 'BS', 'Bracket Label 140x90', 'SS 304', '0.5mm', '140 x 90',
     'Black Epoxy Filled', '398.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-MTR-070', 'BS', 'Meter Face 70x70', 'SS 316', '0.5mm', '70 x 70',
     'Laminated, Clear Coat', '246.00', 'QTN-0396', date(2026, 6, 1), True),
    ('BS-COV-300', 'BS', 'Cover Plate 300x200', 'Mild Steel', '3.0mm', '300 x 200',
     'Black Epoxy Filled', '1180.00', 'QTN-0396', date(2026, 6, 1), True),
]

# --------------------------------------------------------------------------
# RATE CARDS. An item code is priced by quantity BAND, not by one flat rate.
# Each band is approved once and then reused forever, so these are the figures
# a salesperson gets back automatically when they quote inside the band.
#
# Only the hero codes carry a real ladder; every other approved code above gets
# a single 1..(no limit) band at its rate, which is exactly what the data
# migration does to a legacy database.
#
# Note BS-PNL-200 deliberately STOPS at 200: quoting 250 of it falls in no band
# at all, which is what puts a "new band" in the Sales Manager's queue.
# --------------------------------------------------------------------------
RATE_BANDS = {
    'VF-NP-150':  [(1, 100, '420.00'), (101, 300, '380.00'), (301, 1000, '340.00')],
    'BS-PNL-200': [(1, 50, '1500.00'), (51, 200, '1382.00')],
    'TP-NP-120':  [(1, 100, '560.00'), (101, 500, '512.00')],
    'VF-DP-080':  [(1, 100, '175.00'), (101, 200, '111.00'), (201, 500, '95.00')],
}

# --------------------------------------------------------------------------
# THE SHOWCASE ORDER — one Sales Order, ten item codes, three job cards.
#
# This is the shape the mockup shows and the reason JobCard stopped pointing at
# a single SOItem: a card covers a SUBSET of the order's item codes.
#   * 2 items dispatched (card 1, complete, package QR printed)
#   * 3 items in production (card 2 covers two of them; card 3 is an URGENT
#     SPLIT of the third, pulled out with a written reason)
#   * 5 items with no card at all, each blocked for a DIFFERENT reason
#
# state -> what the seeder builds:
#   dispatched    drawing, approved, sheet consumed, on a completed card + QR
#   production    drawing, approved, sheet issued,   on a part-done card
#   urgent        as production, but on its own is_urgent_split card
#   no_drawing    nothing at all           -> 'Awaiting drawing from Design'
#   query         drawing + an OPEN query  -> 'Design query open'
#   not_approved  drawing, not approved    -> 'Design not approved'
#   not_reserved  drawing + approved       -> 'Material not reserved'
#   not_issued    drawing, approved, sheet RESERVED only
#                                          -> 'Material not issued by Stores'
# --------------------------------------------------------------------------
SHOWCASE_CUSTOMER = 'BS'
SHOWCASE_BOOKED_AGO = 20
SHOWCASE_DUE_IN = 11
SHOWCASE_ITEMS = [
    ('BS-PNL-200', 40, 'dispatched'),
    ('BS-RAT-100', 60, 'dispatched'),
    ('BS-NP-160', 35, 'production'),
    ('BS-DP-090', 90, 'production'),
    ('BS-LBL-120', 25, 'urgent'),
    ('BS-TAG-055', 120, 'no_drawing'),
    ('BS-SGN-250', 20, 'query'),
    ('BS-BRK-140', 45, 'not_approved'),
    ('BS-MTR-070', 70, 'not_reserved'),
    ('BS-COV-300', 15, 'not_issued'),
]

# States that consume a sheet out of the stock pool, so seed_sheets can size it.
SHOWCASE_NEEDS_SHEET = {'dispatched', 'production', 'urgent', 'not_issued'}

SHOWCASE_URGENT_REASON = (
    'Customer line stoppage at Bharat Switchgear — the legend plates were '
    'pulled out of the batch and released on their own so the rest of the '
    'order is not held up behind them.'
)

# --------------------------------------------------------------------------
# Sales orders, listed in SO-number order (SO-1031 upwards).
#   idx 8 -> SO-1039, idx 9 -> SO-1040, idx 10 -> SO-1041,
#   idx 11 -> SO-1042, idx 13 -> SO-1044.
#
# The mockups' "Konkan Marine" is not in the customer master, so the SO-1040
# job is booked against Trident Pumps Ltd (see the command's report).
# SO-1035 (the duplicate of SO-1040) and QTN-0411 (the duplicate of QTN-0401)
# are deliberately NOT created.
#
# fields: qtn, customer, item code, salesperson, status, material state,
#         booked days ago, expected delivery in N days, stage note, priority
# --------------------------------------------------------------------------
S = OrderStatus
ORDER_SPECS = [
    # 0
    (None, 'MT', 'MT-LBL-060', 'arjun', S.IN_PRODUCTION, 'consumed', 31, 10,
     'Etching in progress', 2),
    (None, 'VF', 'VF-NP-150', 'arjun', S.IN_PRODUCTION, 'consumed', 29, 9,
     'Colour filling stage', 2),
    (None, 'VF', 'VF-DP-080', 'arjun', S.DISPATCHED, 'consumed', 40, 2,
     'Delivered — LR 4471', 3),
    (None, 'OC', 'OC-TAG-035', 'imran', S.DISPATCH_READY, 'consumed', 35, 5,
     'Ready, awaiting pickup', 2),
    (None, 'VF', 'VF-NP-150', 'arjun', S.IN_PRODUCTION, 'consumed', 27, 8,
     'Etching in progress', 1),
    (None, 'TP', 'TP-SER-050', 'sneha', S.DISPATCH_READY, 'consumed', 23, 4,
     'Ready, awaiting pickup', 3),
    ('QTN-0401', 'TP', 'TP-NP-120', 'sneha', S.PRODUCTION_COMPLETED, 'consumed', 19, 8,
     'QC check in progress', 2),
    (None, 'MT', 'MT-LBL-060', 'arjun', S.IN_PRODUCTION, 'consumed', 21, 1,
     'Etching in progress', 1),                                        # RED
    # 8 -> SO-1039
    (None, 'BS', 'BS-PNL-200', 'imran', S.MATERIAL_RESERVED, 'reserved', 6, 15,
     'Panel plates nested', 2),
    # 9 -> SO-1040 (the Konkan Marine job; QTN-0418)
    ('QTN-0418', 'TP', 'TP-NP-120', 'sneha', S.IN_PRODUCTION, 'consumed', 13, 10,
     'Etching in progress', 1),
    # 10 -> SO-1041
    (None, 'TP', 'TP-NP-120', 'sneha', S.READY_TO_PRODUCE, 'issued', 17, 12,
     'Material issued, queued for floor', 2),
    # 11 -> SO-1042
    (None, 'MT', 'MT-LBL-060', 'arjun', S.PRODUCTION_COMPLETED, 'consumed', 20, 8,
     'QC check in progress', 3),
    # 12
    (None, 'OC', 'OC-TAG-035', 'imran', S.DISPATCH_READY, 'consumed', 24, 5,
     'Ready, awaiting pickup', 2),
    # 13 -> SO-1044
    (None, 'VF', 'VF-NP-150', 'arjun', S.DESIGN_QUERY, None, 9, 20,
     'Query raised on artwork', 2),
    # 14
    (None, 'BS', 'BS-RAT-100', 'imran', S.DESIGN_QUERY, None, 5, 18,
     'Query raised on artwork', 3),
    (None, 'OC', 'OC-NP-110', 'imran', S.READY_TO_PRODUCE, 'issued', 15, 12,
     'Material issued, queued for floor', 3),
    (None, 'MT', 'MT-LBL-060', 'arjun', S.MATERIAL_ISSUED, 'issued', 14, 14,
     'Sheet issued to floor', 3),
    (None, 'VF', 'VF-NP-150', 'arjun', S.MATERIAL_RESERVED, 'reserved', 12, 15,
     'Sheet reserved, awaiting issue', 3),
    (None, 'VF', 'VF-NP-150', 'arjun', S.MATERIAL_RESERVED, 'reserved', 11, 2,
     'Sheet reserved, awaiting issue', 1),                             # RED
    (None, 'MT', 'MT-LBL-060', 'arjun', S.MATERIAL_RESERVED, 'reserved', 10, 15,
     'Nesting done, awaiting issue', 3),
    # 20
    (None, 'MT', 'MT-LBL-060', 'arjun', S.MATERIAL_RESERVED, 'reserved', 9, 16,
     'Nesting done, awaiting issue', 3),
    (None, 'TP', 'TP-NP-120', 'sneha', S.MATERIAL_RESERVED, 'reserved', 8, 15,
     'Sheet reserved, awaiting issue', 2),
    (None, 'OC', 'OC-NP-110', 'imran', S.MATERIAL_RESERVED, 'reserved', 8, 16,
     'Sheet reserved, awaiting issue', 3),
    (None, 'OC', 'OC-TAG-035', 'imran', S.MATERIAL_RESERVED, 'reserved', 7, 15,
     'Awaiting brass sheet', 3),
    (None, 'BS', 'BS-PNL-200', 'imran', S.MATERIAL_RESERVED, 'reserved', 5, 14,
     'Panel plates nested', 2),
    (None, 'BS', 'BS-PNL-200', 'imran', S.MATERIAL_RESERVED, 'reserved', 4, 15,
     'Panel plates nested', 3),
    (None, 'BS', 'BS-RAT-100', 'imran', S.MATERIAL_RESERVED, 'reserved', 3, 16,
     'Sheet reserved, awaiting issue', 3),
    (None, 'VF', 'VF-DP-080', 'arjun', S.DESIGN_APPROVED, None, 7, 18,
     'Drawing approved, nesting next', 3),
    (None, 'TP', 'TP-SER-050', 'sneha', S.DESIGN_APPROVED, None, 6, 18,
     'Drawing approved, nesting next', 3),
    (None, 'MT', 'MT-LBL-060', 'arjun', S.DESIGN_APPROVED, None, 5, 6,
     'Drawing approved, nesting next', 1),                             # RED
    # 30
    (None, 'OC', 'OC-NP-110', 'imran', S.DESIGN_APPROVED, None, 4, 19,
     'Drawing approved, nesting next', 3),
    (None, 'VF', 'VF-NP-150', 'arjun', S.IN_DESIGN, None, 6, 20,
     'Artwork under review', 3),
    (None, 'TP', 'TP-NP-120', 'sneha', S.IN_DESIGN, None, 5, 20,
     'Artwork under review', 2),
    (None, 'BS', 'BS-PNL-200', 'imran', S.IN_DESIGN, None, 4, 4,
     'Artwork under review', 1),                                       # RED
    (None, 'MT', 'MT-LBL-060', 'arjun', S.IN_DESIGN, None, 3, 21,
     'Artwork under review', 3),
    (None, 'OC', 'OC-TAG-035', 'imran', S.IN_DESIGN, None, 2, 22,
     'Not started — in design queue', 3),
    (None, 'VF', 'VF-DP-080', 'arjun', S.IN_DESIGN, None, 0, 23,
     'Not started — in design queue', 3),
    (None, 'TP', 'TP-SER-050', 'sneha', S.IN_DESIGN, None, 0, 24,
     'Not started — in design queue', 3),
    # 38-40: booked, drawing never arrived -> health is AMBER "awaiting drawing"
    (None, 'BS', 'BS-RAT-100', 'imran', S.BOOKED, None, 0, 26,
     'Drawing not yet received', 3),
    (None, 'MT', 'MT-LBL-060', 'arjun', S.BOOKED, None, 0, 26,
     'Drawing not yet received', 3),
    # 40
    (None, 'TP', 'TP-NP-120', 'sneha', S.BOOKED, None, 0, 25,
     'Drawing not yet received', 2),
    (None, 'OC', 'OC-NP-110', 'imran', S.ON_HOLD, None, 0, 22,
     'Held at customer request', 3),
    (None, 'VF', 'VF-NP-150', 'arjun', S.ON_HOLD, None, 16, 18,
     'Held — awaiting revised PO', 3),
    (None, 'MT', 'MT-LBL-060', 'arjun', S.CANCELLED, 'released', 18, 12,
     'Cancelled by customer', 3),
    (None, 'VF', 'VF-DP-080', 'arjun', S.CANCELLED, 'released', 13, 14,
     'Cancelled by customer', 3),
]

HOLD_REASONS = {
    41: 'Customer asked to pause pending a design freeze on the compressor cover.',
    42: 'Revised PO awaited — quantity increased verbally, nothing in writing yet.',
}
CANCEL_REASONS = {
    43: 'Customer cancelled the machine build; labels no longer required.',
    44: 'Duplicate PO raised by the customer — cancelled against PO/2026/0788.',
}

# Orders whose design job has been picked up (vs. sitting untouched in the queue).
DESIGN_IN_PROGRESS = {31, 32, 33, 34}

# --------------------------------------------------------------------------
# The 25 minted quotations that never became orders. With the four pinned
# PENDING_APPROVAL heroes that makes 29 live quotations — every filter chip on
# "My Quotations" has rows behind it.
#   fields: customer, item code, salesperson, status, quote days ago, qty
# --------------------------------------------------------------------------
Q = QuotationStatus
LIVE_QUOTES = [
    ('VF', 'VF-NP-150', 'arjun', Q.DRAFT, 1, 150),
    ('BS', 'BS-PNL-200', 'imran', Q.DRAFT, 2, 60),
    ('MT', 'MT-LBL-060', 'arjun', Q.DRAFT, 3, 220),
    ('OC', 'OC-NP-110', 'imran', Q.DRAFT, 4, 130),
    ('TP', 'TP-SER-050', 'sneha', Q.REJECTED, 6, 260),
    ('VF', 'VF-NP-150', 'arjun', Q.APPROVED, 8, 180),
    ('BS', 'BS-RAT-100', 'imran', Q.APPROVED, 9, 140),
    ('MT', 'MT-LBL-060', 'arjun', Q.APPROVED, 10, 240),
    ('TP', 'TP-NP-120', 'sneha', Q.APPROVED, 11, 160),
    ('OC', 'OC-TAG-035', 'imran', Q.APPROVED, 12, 280),
    ('VF', 'VF-DP-080', 'arjun', Q.APPROVED, 13, 200),
    ('BS', 'BS-PNL-200', 'imran', Q.SENT_TO_CUSTOMER, 14, 110),
    ('MT', 'MT-LBL-060', 'arjun', Q.SENT_TO_CUSTOMER, 15, 250),
    ('TP', 'TP-NP-120', 'sneha', Q.SENT_TO_CUSTOMER, 16, 170),
    ('OC', 'OC-NP-110', 'imran', Q.SENT_TO_CUSTOMER, 17, 190),
    ('VF', 'VF-NP-150', 'arjun', Q.SENT_TO_CUSTOMER, 18, 210),
    ('BS', 'BS-RAT-100', 'imran', Q.AWAITING_PO, 19, 120),
    ('MT', 'MT-LBL-060', 'arjun', Q.AWAITING_PO, 20, 230),
    ('TP', 'TP-SER-050', 'sneha', Q.AWAITING_PO, 21, 270),
    ('OC', 'OC-TAG-035', 'imran', Q.AWAITING_PO, 22, 290),
    ('VF', 'VF-DP-080', 'arjun', Q.AWAITING_PO, 23, 205),
    ('BS', 'BS-PNL-200', 'imran', Q.AWAITING_PO, 24, 115),
    # Ready to convert — PO and drawing both in.
    ('TP', 'TP-NP-120', 'sneha', Q.PO_UPLOADED, 5, 165),
    # PO in, drawing still missing — the "awaiting drawing" case.
    ('MT', 'MT-LBL-060', 'arjun', Q.PO_UPLOADED, 7, 245),
    ('VF', 'VF-NP-150', 'arjun', Q.PO_REJECTED, 4, 175),
]

# --------------------------------------------------------------------------
# Stock. `target_available` is what must still be AVAILABLE once every
# reservation/issue/consumption below has been applied; the number of sheets
# actually created per combination is target_available + demand, so the strip
# reads 61 available out of 86 no matter how the orders move.
# --------------------------------------------------------------------------
SHEET_PLAN = [
    # material, thickness, catalogue size, target available
    ('SS 304', '2.0mm', '8 x 4 ft', 4),
    ('SS 304', '1.5mm', '8 x 4 ft', 9),
    ('SS 304', '0.5mm', '6 x 3 ft', 8),
    ('SS 316', '0.5mm', '6 x 3 ft', 7),
    ('Brass', '1.5mm', '4 x 2 ft', 2),
    ('Brass', '1.0mm', '4 x 2 ft', 6),
    ('Aluminium', '1.0mm', '8 x 4 ft', 13),
    ('Mild Steel', '3.0mm', '10 x 4 ft', 3),
    ('Aluminium', '0.5mm', '6 x 3 ft', 9),
]
OFFCUT_TARGET = 29

# Admin-set minimums. Three combinations sit under them, so the low-stock
# alert fires on SS 304 2.0mm, Brass 1.5mm and Mild Steel 3.0mm.
REORDER_PLAN = [
    ('SS 304', '2.0mm', 6),      # 4 available  -> below
    ('Brass', '1.5mm', 5),       # 2 available  -> below
    ('Mild Steel', '3.0mm', 5),  # 3 available  -> below
    ('SS 304', '1.5mm', 6),      # 9 available
    ('SS 316', '0.5mm', 4),      # 7 available
    ('Aluminium', '1.0mm', 8),   # 13 available
]

OFFCUT_NOTES = ['18 x 14 in', '10 x 8 in', '22 x 9 in', '14 x 11 in', '8 x 6 in',
                '20 x 12 in', '16 x 7 in', '12 x 10 in']


class Command(BaseCommand):
    help = 'Seed demo masters, users, quotations, orders, sheets and job cards.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete existing business data first')

    # ------------------------------------------------------------------
    @transaction.atomic
    def handle(self, *args, **options):
        self.rng = random.Random(20260810)

        if options['reset']:
            self.stdout.write('Resetting business tables...')
            self.wipe()
        elif Quotation.objects.exists():
            self.stdout.write(self.style.WARNING(
                'Already seeded — nothing to do. Re-run with --reset to rebuild.'))
            self.report()
            return

        self.check_specs()

        users = self.seed_users()
        masters = self.seed_masters()
        codes = self.seed_item_codes(masters, users)
        self.seed_sequences()
        sheets = self.seed_sheets(masters, users)
        quotes = self.seed_pinned_quotations(users, masters, codes)
        orders = self.seed_orders(users, masters, codes, quotes)
        self.seed_design(users, orders, sheets)
        self.seed_production(users, orders)
        self.showcase = self.seed_showcase(users, masters, codes, sheets)
        self.seed_dispatch(users, orders)
        self.seed_live_quotations(users, masters, codes)
        self.seed_reorder_levels(masters)
        self.seed_alerts(users, quotes, orders)

        self.report()

    # ------------------------------------------------------------------
    def wipe(self):
        for model in (
            JobCardStation, JobCardItem, JobCard, DispatchRecord, MaterialReservation,
            QueryMessage, DesignQuery, DrawingRevision, SOItem, SalesOrder,
            PriceTier, QuotationItem, Quotation, ItemCodeRate, ItemCode, ReorderLevel,
            Sheet, AuditLog, Notification, Sequence,
        ):
            model.objects.all().delete()
        # Customers are a master, not business data, so they survive a normal
        # run — but a stray one would make the "5 customers" strip a lie.
        Customer.objects.exclude(name__in=[c[1] for c in CUSTOMERS]).delete()

    def check_specs(self):
        """Fail loudly here rather than leaving a half-legal database behind."""
        approved_on = {c[0]: c[9] for c in ITEM_CODES}
        for idx, spec in enumerate(ORDER_SPECS):
            code, booked_ago = spec[2], spec[6]
            on = approved_on.get(code)
            assert on is not None, f'order {idx} uses unapproved code {code}'
            assert on <= ago(booked_ago), (
                f'order {idx}: {code} approved {on}, order booked {ago(booked_ago)}')
        mat = Counter(s[5] for s in ORDER_SPECS)
        assert mat['reserved'] == 11 and mat['issued'] == 3 and mat['consumed'] == 11, mat

    # ------------------------------------------------------------------
    def seed_users(self):
        spec = [
            ('arjun', 'Arjun', 'Patil', 'is_salesman'),
            ('imran', 'Imran', 'Sheikh', 'is_salesman'),
            ('sneha', 'Sneha', 'Rao', 'is_salesman'),
            ('vikram', 'Vikram', 'Mehta', 'is_sales_manager'),
            ('priya', 'Priya', 'Nair', 'is_accounts'),
            ('meera', 'Meera', 'Deshpande', 'is_designer'),
            ('sunil', 'Sunil', 'Rao', 'is_production_manager'),
            ('ramesh', 'Ramesh', 'Tawde', 'is_floor_manager'),
            ('deepak', 'Deepak', 'Salvi', 'is_stores'),
            ('anita', 'Anita', 'Kale', 'is_qa'),
            # Shop-floor operators — no department screens, but pickable as the
            # operator on a job card station.
            ('ganesh', 'Ganesh', 'Pawar', 'is_operator'),
            ('farida', 'Farida', 'Sayyed', 'is_operator'),
            ('ravik', 'Ravi', 'Kadam', 'is_operator'),
            ('sudha', 'Sudha', 'More', 'is_operator'),
            ('imrank', 'Imran', 'Khan', 'is_operator'),
        ]
        users = {}
        for num, (username, first, last, flag) in enumerate(spec, start=1):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'first_name': first, 'last_name': last,
                          'email': f'{username}@chemicaletching.local',
                          'employee_id': f'EMP-{num:03d}'},
            )
            if created:
                user.set_password('demo1234')
            user.first_name, user.last_name = first, last
            user.employee_id = f'EMP-{num:03d}'
            setattr(user, flag, True)
            user.save()
            users[username] = user

        admin = User.objects.filter(username='admin').first()
        if admin is None:
            admin = User.objects.create_superuser(
                'admin', 'admin@chemicaletching.local', 'demo1234')
            admin.employee_id = 'EMP-000'
            admin.save()
        users['admin'] = admin
        users['operators'] = [users[u] for u in
                              ('ganesh', 'farida', 'ravik', 'sudha', 'imrank')]
        return users

    def seed_masters(self):
        def bulk(model, names):
            return {n: model.objects.get_or_create(name=n)[0] for n in names}

        materials = bulk(Material, ['SS 304', 'SS 316', 'Brass', 'Aluminium', 'Mild Steel'])
        grades = bulk(Grade, ['Commercial', 'Prime', 'Secondary'])
        finishes = bulk(Finish, [
            'Black Epoxy Filled', 'Anodized', 'Laminated, Clear Coat', 'Natural Finish',
        ])
        racks = bulk(Rack, ['Rack A-1', 'Rack A-2', 'Rack B-2', 'Rack B-3',
                            'Rack C-1', 'Rack C-2', 'Shop floor'])

        thicknesses = {}
        for name, mm in [('0.5mm', '0.50'), ('1.0mm', '1.00'), ('1.5mm', '1.50'),
                         ('2.0mm', '2.00'), ('3.0mm', '3.00')]:
            thicknesses[name] = Thickness.objects.get_or_create(
                name=name, defaults={'value_mm': Decimal(mm)})[0]

        sizes = {}
        for name, length, width in [('8 x 4 ft', 2438, 1219), ('6 x 3 ft', 1829, 914),
                                    ('4 x 2 ft', 1219, 610), ('10 x 4 ft', 3048, 1219)]:
            sizes[name] = SheetSize.objects.get_or_create(
                name=name, defaults={'length_mm': length, 'width_mm': width})[0]

        suppliers = {n: Supplier.objects.get_or_create(name=n)[0] for n in
                     ['Mehta Metals', 'Shree Steel Traders', 'Balaji Metal Co.',
                      'Konkan Steel', 'Deccan Alloys']}

        customers = {}
        for key, name, addr, city, state, pin, gstin, contact, phone, email, limit \
                in CUSTOMERS:
            obj, _ = Customer.objects.get_or_create(name=name, defaults={})
            obj.address, obj.city, obj.state, obj.pincode = addr, city, state, pin
            obj.gstin, obj.contact_person = gstin, contact
            obj.phone, obj.email, obj.credit_limit = phone, email, limit
            obj.is_active = True
            obj.save()
            customers[key] = obj

        stations = []
        for idx, (name, group) in enumerate(STATIONS, start=1):
            station, _ = ProcessStation.objects.get_or_create(name=name)
            station.sequence, station.group, station.is_active = idx, group, True
            station.save()
            stations.append(station)

        return {'materials': materials, 'thicknesses': thicknesses, 'grades': grades,
                'finishes': finishes, 'racks': racks, 'sizes': sizes,
                'suppliers': suppliers, 'customers': customers, 'stations': stations}

    def seed_item_codes(self, m, users):
        """Every approved code gets a rate CARD, not a single rate.

        The hero codes carry a real ladder out of RATE_BANDS; the rest get one
        open-ended band at their historic rate. Pending codes get no band —
        there is nothing approved to put on the card yet.
        """
        codes = {}
        for (code, cust, desc, mat, thk, size, fin, rate, via, on, active) in ITEM_CODES:
            approved = rate is not None
            codes[code] = ItemCode.objects.create(
                customer=m['customers'][cust], code=code, description=desc,
                material=m['materials'][mat], thickness=m['thicknesses'][thk],
                size=size, finish=m['finishes'][fin], is_active=active,
                approval_state=(ItemCodeApproval.APPROVED if approved
                                else ItemCodeApproval.PENDING),
                approved_rate=Decimal(rate) if approved else None,
                approved_on=on, approved_via=via,
                approval_source=ApprovalSource.QUOTATION,
                approved_by=users['vikram'] if approved else None,
            )
            if not approved:
                continue

            bands = RATE_BANDS.get(code) or [(1, None, rate)]
            ItemCodeRate.objects.bulk_create([
                ItemCodeRate(
                    item_code=codes[code], qty_from=qty_from, qty_to=qty_to,
                    rate_per_unit=Decimal(band_rate),
                    approved_rate=Decimal(band_rate),
                    approval_state=ItemCodeApproval.APPROVED,
                    approved_via=via, approved_on=on,
                    approved_by=users['vikram'],
                    approval_source=ApprovalSource.QUOTATION,
                )
                for qty_from, qty_to, band_rate in bands
            ])
            # The legacy flat field is only a mirror of the lowest band now.
            codes[code].refresh_approved_rate()
        return codes

    # ------------------------------------------------------------------
    @staticmethod
    def band_rate(code_obj, qty):
        """The approved band rate for `qty` — what the form would auto-fill."""
        band = code_obj.approved_band_for(qty)
        if band is not None:
            return band.approved_rate
        return code_obj.approved_rate

    @staticmethod
    def fit_qty(code_obj, qty):
        """Pull a generated quantity back inside the code's rate card.

        Only the seeder needs this: a demo order quoted outside every band
        would look like it was booked without an approved rate.
        """
        if code_obj.approved_band_for(qty) is not None:
            return qty
        top = code_obj.approved_bands.last()
        if top is None:
            return qty
        return min(qty, top.qty_to) if top.qty_to else max(qty, top.qty_from)

    def seed_sequences(self):
        """Pre-seed the counters so minted numbers land clear of the heroes."""
        for prefix, last, pad in [('QTN', 422, 4), ('SO', 1030, 4),
                                  ('JC', 500, 4), ('SHT', 100, 4), ('OFF', 100, 4)]:
            Sequence.objects.update_or_create(
                prefix=prefix, defaults={'last_value': last, 'padding': pad})

    @staticmethod
    def rewind_qtn():
        """create_revision() mints a number then throws it away — take it back."""
        Sequence.objects.filter(prefix='QTN').update(last_value=F('last_value') - 1)

    # ------------------------------------------------------------------
    def seed_sheets(self, m, users):
        """Create the stock pool: 86 sheets, 29 of them offcuts with lineage."""
        demand = Counter()
        code_combo = {c[0]: (c[3], c[4]) for c in ITEM_CODES}
        for spec in ORDER_SPECS:
            if spec[5] in ('reserved', 'issued', 'consumed'):
                demand[code_combo[spec[2]]] += 1
        # The showcase order draws from the same pool.
        for code, _qty, state in SHOWCASE_ITEMS:
            if state in SHOWCASE_NEEDS_SHEET:
                demand[code_combo[code]] += 1

        # Spread the offcuts round-robin, never more than the combination's
        # available headroom so every offcut still has a full parent sheet.
        offcuts = {(mat, thk): 0 for mat, thk, _, _ in SHEET_PLAN}
        caps = {(mat, thk): avail - 1 for mat, thk, _, avail in SHEET_PLAN}
        placed = 0
        while placed < OFFCUT_TARGET:
            moved = False
            for mat, thk, _, _ in SHEET_PLAN:
                if placed >= OFFCUT_TARGET:
                    break
                if offcuts[(mat, thk)] < caps[(mat, thk)]:
                    offcuts[(mat, thk)] += 1
                    placed += 1
                    moved = True
            if not moved:                                   # pragma: no cover
                break

        racks = list(m['racks'].values())
        suppliers = list(m['suppliers'].values())
        grades = [m['grades']['Prime'], m['grades']['Commercial'], m['grades']['Secondary']]
        pool = {}
        made = 0
        for mat, thk, size_name, avail in SHEET_PLAN:
            total = avail + demand[(mat, thk)]
            n_off = offcuts[(mat, thk)]
            n_full = total - n_off
            bucket, parent = [], None
            for i in range(n_full):
                received = ago(4 + (made * 3) % 90)
                sheet = Sheet.objects.create(
                    material=m['materials'][mat], thickness=m['thicknesses'][thk],
                    grade=grades[made % 3], size=m['sizes'][size_name],
                    supplier=suppliers[made % len(suppliers)],
                    rack=racks[made % len(racks)],
                    received_on=received, purchase_date=received - timedelta(days=2),
                    status=SheetStatus.AVAILABLE, logged_by=users['deepak'],
                    label_batch=f'BATCH-2026-{233 + made // 12:04d}',
                )
                parent = parent or sheet
                bucket.append(sheet)
                made += 1
            for i in range(n_off):
                note = OFFCUT_NOTES[(made + i) % len(OFFCUT_NOTES)]
                inches = [int(x) for x in note.replace(' in', '').split(' x ')]
                bucket.append(Sheet.objects.create(
                    material=m['materials'][mat], thickness=m['thicknesses'][thk],
                    grade=parent.grade, is_offcut=True, parent=parent,
                    size_note=note, length_mm=inches[0] * 25, width_mm=inches[1] * 25,
                    rack=m['racks']['Rack A-2'], status=SheetStatus.AVAILABLE,
                    received_on=ago(3 + (made * 2) % 40), logged_by=users['deepak'],
                    label_batch=f'BATCH-2026-{233 + made // 12:04d}',
                    remarks=f'Leftover from {parent.uid}',
                ))
                made += 1
            pool[(mat, thk)] = bucket
        return pool

    def take_sheet(self, pool, material, thickness):
        for sheet in pool[(material.name, thickness.name)]:
            sheet.refresh_from_db()
            if sheet.status == SheetStatus.AVAILABLE:
                return sheet
        raise RuntimeError(f'no available sheet for {material} {thickness}')

    # ------------------------------------------------------------------
    def quote_defaults(self, qdate):
        return {
            'quote_date': qdate,
            'valid_until': qdate + timedelta(days=30),
            'payment_terms': '30% Advance, Balance against Docs',
            'delivery_terms': 'Ex-Works',
            'lead_time': '3-4 Weeks from PO + Artwork Approval',
            'tooling_charges': Decimal('8500.00'),
            'packing_forwarding': Decimal('5000.00'),
            'freight': Decimal('0.00'),
            'order_type': OrderType.NEW,
        }

    def add_line(self, quotation, code_obj, qty, rate, position=1):
        """One line plus the three-slab price ladder every quote carries.

        The ladder always has the same shape (0-100 / 101-300 / 301+) and always
        falls with volume, but it is anchored so that the slab the ordered
        quantity lands in holds `rate` exactly — otherwise the rate printed on
        the quote and the rate the Sales Manager is asked to approve differ.
        """
        item = QuotationItem.objects.create(
            quotation=quotation, position=position, item_code=code_obj,
            description=code_obj.description, material=code_obj.material,
            thickness=code_obj.thickness, finish=code_obj.finish, size=code_obj.size,
            artwork_ref=f'ART-{code_obj.code}',
            quantity=qty,
        )
        base = Decimal(rate)

        def scaled(factor):
            return (base * Decimal(factor)).quantize(Decimal('0.01'))

        if qty <= 100:
            rates = (base, scaled('0.92'), scaled('0.85'))
        elif qty <= 300:
            rates = (scaled('1.15'), base, scaled('0.92'))
        else:
            rates = (scaled('1.30'), scaled('1.13'), base)
        PriceTier.objects.bulk_create([
            PriceTier(item=item, qty_from=0, qty_to=100, rate_per_unit=rates[0]),
            PriceTier(item=item, qty_from=101, qty_to=300, rate_per_unit=rates[1]),
            PriceTier(item=item, qty_from=301, qty_to=None, rate_per_unit=rates[2]),
        ])
        return item

    def seed_pinned_quotations(self, users, m, codes):
        """The quotations the client has already been shown, by exact number."""
        now = timezone.now()
        quotes = {}

        def pinned(number, cust, sales, status, qdate, lines, **extra):
            fields = self.quote_defaults(qdate)
            fields.update(extra)
            q = Quotation.objects.create(
                number=number, customer=m['customers'][cust],
                salesperson=users[sales], status=status, **fields)
            for pos, (code, qty, rate) in enumerate(lines, start=1):
                self.add_line(q, codes[code], qty, rate, pos)
            quotes[number] = q
            return q

        # The two hero jobs. QTN-0401 is the Trident job — QTN-0411 was the same
        # job under a second number and is not created. The quotations that
        # locked in the item-code rates (QTN-0389/0394/0398/0402/0405/0416) are
        # pre-system history: they survive only as ItemCode.approved_via, which
        # is why the QTN sequence is parked at 422 and never mints them.
        pinned('QTN-0401', 'TP', 'sneha', QuotationStatus.CONVERTED, date(2026, 7, 16),
               [('TP-NP-120', 200, '512.00')],
               approved_at=at(date(2026, 7, 18)), expected_delivery=date(2026, 8, 18))
        pinned('QTN-0418', 'TP', 'sneha', QuotationStatus.CONVERTED, date(2026, 7, 24),
               [('TP-NP-120', 260, '512.00')],
               approved_at=at(date(2026, 7, 26)), expected_delivery=date(2026, 8, 20))

        # --- the Sales Manager's approval queue -----------------------------
        # QTN-0417: rejected on margin, revised and resubmitted as v2. The v2
        # rate is the one already approved on the 101-300 band, so that line
        # needs no sign-off at all — an approved band is reused, not re-asked.
        v1 = pinned('QTN-0417', 'VF', 'arjun', QuotationStatus.DRAFT, date(2026, 8, 2),
                    [('VF-NP-150', 200, '340.00')])
        v1.submit()
        v1.reject(users['vikram'], 'Margin too low - revise rate')
        v2 = v1.create_revision()
        self.rewind_qtn()
        v2.items.all().delete()
        self.add_line(v2, codes['VF-NP-150'], 200, '380.00')
        v2.quote_date = date(2026, 8, 6)
        v2.valid_until = date(2026, 9, 5)
        v2.status = QuotationStatus.PENDING_APPROVAL
        v2.submitted_at = now - timedelta(days=3)      # 07 Aug 2026 -> 3d pending
        v2.expected_delivery = day(28)
        v2.save()
        quotes['QTN-0417'] = v2

        # QTN-0419: approved code quoted at a revised rate, so it needs re-approval.
        b1 = pinned('QTN-0419', 'BS', 'imran', QuotationStatus.DRAFT, date(2026, 8, 3),
                    [('BS-RAT-100', 45, '432.00')],
                    tooling_charges=Decimal('0.00'),
                    packing_forwarding=Decimal('0.00'))
        b1.submit()
        b1.reject(users['vikram'], 'Rate revision needs the costing sheet attached.')
        b2 = b1.create_revision()
        self.rewind_qtn()
        b2.items.all().delete()
        self.add_line(b2, codes['BS-RAT-100'], 45, '468.00')
        b2.quote_date = date(2026, 8, 7)
        b2.valid_until = date(2026, 9, 6)
        b2.status = QuotationStatus.PENDING_APPROVAL
        b2.submitted_at = now - timedelta(days=2)
        b2.expected_delivery = day(30)
        b2.save()
        codes['BS-RAT-100'].propose_revision(
            Decimal('468.00'), 'Aluminium coil price up 8% — revised on QTN-0419')
        quotes['QTN-0419'] = b2

        # QTN-0420: an APPROVED code quoted at a quantity its rate card does
        # not reach. BS-PNL-200 is banded 1-50 and 51-200; 250 falls in NO
        # band, so a brand-new 101-300 band is proposed and the Sales Manager
        # has to price it. The 1-50 and 51-200 bands are not touched.
        q20 = pinned('QTN-0420', 'BS', 'imran', QuotationStatus.PENDING_APPROVAL,
                     date(2026, 8, 5), [('BS-PNL-200', 250, '1290.00')],
                     tooling_charges=Decimal('0.00'),
                     packing_forwarding=Decimal('6500.00'))
        q20.submitted_at = now - timedelta(days=4)
        q20.expected_delivery = day(27)
        q20.save()

        # QTN-0421 / QTN-0422: brand new codes, first approval.
        q21 = pinned('QTN-0421', 'BS', 'imran', QuotationStatus.PENDING_APPROVAL,
                     date(2026, 8, 6), [('BS-TAG-075', 150, '210.00')],
                     tooling_charges=Decimal('3500.00'),
                     packing_forwarding=Decimal('0.00'))
        q21.submitted_at = now - timedelta(days=2)
        q21.expected_delivery = day(26)
        q21.save()

        q22 = pinned('QTN-0422', 'MT', 'arjun', QuotationStatus.PENDING_APPROVAL,
                     date(2026, 8, 8), [('MT-NP-090', 80, '265.00')],
                     tooling_charges=Decimal('0.00'),
                     packing_forwarding=Decimal('0.00'))
        q22.submitted_at = now - timedelta(days=1)
        q22.expected_delivery = day(29)
        q22.save()

        codes['BS-TAG-075'].propose_revision(Decimal('210.00'), 'New code — first quote')
        codes['MT-NP-090'].propose_revision(Decimal('265.00'), 'New code — first quote')
        return quotes

    # ------------------------------------------------------------------
    def seed_orders(self, users, m, codes, quotes):
        """45 sales orders in SO-number order, minted through the real service."""
        orders = []
        for idx, (qtn, cust, code, sales, status, mat_state,
                  booked_ago, due_in, note, priority) in enumerate(ORDER_SPECS):
            code_obj = codes[code]
            booked_on = ago(booked_ago)
            if qtn:
                quotation = quotes[qtn]
            else:
                qty = self.fit_qty(code_obj, 110 + (idx * 13) % 180)
                quotation = Quotation.objects.create(
                    customer=m['customers'][cust], salesperson=users[sales],
                    status=QuotationStatus.APPROVED,
                    approved_by=users['vikram'],
                    approved_at=at(booked_on - timedelta(days=4)),
                    expected_delivery=day(due_in),
                    **self.quote_defaults(booked_on - timedelta(days=8)))
                self.add_line(quotation, code_obj, qty, self.band_rate(code_obj, qty))

            quotation.po_number = f'PO/2026/{700 + idx:04d}'
            quotation.po_date = booked_on - timedelta(days=1)
            quotation.po_file = f'po/PO-2026-{700 + idx:04d}.pdf'
            # 38-40 are booked against a PO whose drawing never arrived.
            if status != OrderStatus.BOOKED:
                quotation.drawing_file = f'drawings/{code}-customer.pdf'
            quotation.expected_delivery = day(due_in)
            quotation.order_type = OrderType.REPEAT if idx % 5 == 0 else OrderType.NEW
            quotation.save()

            order = SalesOrder.mint_from_quotation(
                quotation, users['priya'],
                expected_delivery=day(due_in), designer=users['meera'])
            order.booked_on = booked_on
            order.priority = priority
            order.stage_note = note
            order.design_note = f'{code} — {code_obj.description}'
            order.save(update_fields=['booked_on', 'priority', 'stage_note',
                                      'design_note', 'updated_at'])
            orders.append((idx, order, status, mat_state))
        return orders

    # ------------------------------------------------------------------
    def seed_design(self, users, orders, pool):
        """Drawings, design queries and the two-step material handoff."""
        meera, deepak, sunil = users['meera'], users['deepak'], users['sunil']
        past_design = {
            OrderStatus.DESIGN_APPROVED, OrderStatus.MATERIAL_RESERVED,
            OrderStatus.MATERIAL_ISSUED, OrderStatus.READY_TO_PRODUCE,
            OrderStatus.IN_PRODUCTION, OrderStatus.PRODUCTION_COMPLETED,
            OrderStatus.DISPATCH_READY, OrderStatus.DISPATCHED,
        }

        for idx, order, status, mat_state in orders:
            booked = order.booked_on
            for item in order.items.all():
                # BOOKED jobs have no drawing at all — that is what makes their
                # health AMBER "awaiting drawing" rather than green.
                if status != OrderStatus.BOOKED:
                    # Rev 00 is the customer's own reference that came in with
                    # the PO; it is never the current drawing. The DESIGNER
                    # owns the drawing that production works to (Rev 01).
                    DrawingRevision.objects.create(
                        so_item=item, revision=0, source='Customer',
                        file=f'drawings/{item.item_code.code}-customer.pdf',
                        uploaded_by=order.salesperson, is_current=False,
                        note='Customer reference received with the PO')
                    DrawingRevision.objects.create(
                        so_item=item, revision=1, source='Design',
                        file=f'drawings/{item.item_code.code}-rev01.pdf',
                        uploaded_by=meera, is_current=True,
                        note='Hole positions corrected, text re-kerned')
                    item.holes = '2 x d3mm'
                    item.drilling_detail = 'Corner holes, 5mm from edge'

                if status in past_design:
                    item.design_started_at = at(booked + timedelta(days=1), 9)
                    item.design_approved = True
                    item.design_approved_at = at(booked + timedelta(days=3), 16)
                    item.design_approved_by = meera
                elif idx in DESIGN_IN_PROGRESS:
                    item.design_started_at = at(booked + timedelta(days=1), 11)
                item.save()

                if mat_state:
                    sheet = self.take_sheet(pool, item.material, item.thickness)
                    res = MaterialReservation.objects.create(
                        so_item=item, sheet=sheet, reserved_by=meera)
                    if mat_state in ('issued', 'consumed'):
                        res.issue(deepak)
                    if mat_state == 'consumed':
                        res.consume(sunil)

            if status == OrderStatus.DESIGN_QUERY:
                query = DesignQuery.raise_for(
                    order, meera,
                    'Artwork text height not legible at 4mm',
                    'The customer artwork uses 1.2mm stroke at 4mm cap height. '
                    'Etching will close it up — can we go to 5mm, or is the '
                    'plate size fixed?',
                    so_item=order.items.first())
                QueryMessage.objects.filter(query=query).update(
                    created_at=at(ago(2), 14))

            order.status = status
            order.save(update_fields=['status', 'updated_at'])

        # A little query history on jobs that have since moved on, so the
        # design screens are not empty of closed threads.
        for idx, order, status, _ in orders:
            if idx in (0, 6, 11):
                q = DesignQuery.raise_for(
                    order, users['meera'], 'Confirm serial number range',
                    'Is the serial block 0001-0250 or 1001-1250?',
                    so_item=order.items.first())
                q.reply(order.salesperson, 'Confirmed 1001-1250 per the customer mail.')
                q.close()

    # ------------------------------------------------------------------
    def seed_production(self, users, orders):
        """Job cards for everything at or past the floor."""
        stations = list(ProcessStation.objects.filter(is_active=True).order_by('sequence'))
        operators = users['operators']
        floor, prod = users['ramesh'], users['sunil']
        on_floor = {
            OrderStatus.IN_PRODUCTION: 'partial',
            OrderStatus.PRODUCTION_COMPLETED: 'all',
            OrderStatus.DISPATCH_READY: 'all',
            OrderStatus.DISPATCHED: 'all',
        }
        op = 0
        for idx, order, status, _ in orders:
            how = on_floor.get(status)
            if how is None:
                continue
            issued = [i for i in order.items.all() if i.material_issued]
            for item in issued:
                # One card per item on these single-line orders — but minted
                # through the real multi-item helper, because a one-item card
                # is just the smallest possible subset.
                card = self.run_card(
                    [item], stations, prod, floor, operators, op,
                    released_at=at(order.booked_on + timedelta(days=5), 11),
                    priority=order.priority,
                    take=None if how == 'all' else 3 + (idx % 5),
                )
                op += card.done_count + 1
                self.backdate_card(card, order.booked_on)

            # complete_station() calls refresh_status(), which would push a
            # finished card straight to DISPATCH_READY. Restate what we meant.
            order.status = status
            order.save(update_fields=['status', 'updated_at'])

    def run_card(self, items, stations, prod, floor, operators, op, released_at,
                 priority=None, take=None, is_urgent_split=False, urgent_reason=''):
        """Mint one job card covering `items` and walk it down the station list.

        `take=None` finishes every applicable station; an integer leaves the
        card mid-run with the next station IN_PROGRESS.
        """
        card = JobCard.create_for(
            items, prod, priority=priority,
            is_urgent_split=is_urgent_split, urgent_reason=urgent_reason,
        )
        card.build_stations(stations)
        JobCard.objects.filter(pk=card.pk).update(released_at=released_at)
        card.start_work(floor)

        rows = list(card.stations.all())
        skip = {r.pk for r in rows if r.station.name == SKIPPED_STATION}
        card.confirm_stations([r.pk for r in rows if r.pk not in skip])

        pending = list(card.stations.filter(status=StationStatus.PENDING))
        n = len(pending) if take is None else min(take, len(pending))
        for row in pending[:n]:
            card.complete_station(row, operators[op % len(operators)])
            op += 1
        if n < len(pending):
            nxt = card.stations.filter(status=StationStatus.PENDING).first()
            if nxt:
                nxt.status = StationStatus.IN_PROGRESS
                nxt.operator = operators[op % len(operators)]
                nxt.started_at = at(TODAY, 9)
                nxt.save(update_fields=['status', 'operator', 'started_at',
                                        'updated_at'])
        card.refresh_from_db()
        return card

    def backdate_card(self, card, booked_on):
        """Give the stations believable clock times instead of a single instant."""
        start = at(booked_on + timedelta(days=6), 9)
        cursor = start
        for row in card.stations.filter(status=StationStatus.DONE).order_by('sequence'):
            minutes = self.rng.choice([12, 18, 25, 34, 41, 55, 70, 95])
            JobCardStation.objects.filter(pk=row.pk).update(
                started_at=cursor,
                completed_at=cursor + timedelta(minutes=minutes),
                duration_seconds=minutes * 60)
            cursor += timedelta(minutes=minutes + 6)
        JobCard.objects.filter(pk=card.pk).update(
            started_at=start, stations_confirmed_at=start + timedelta(minutes=10))
        card.refresh_from_db()
        if card.completed_at:
            JobCard.objects.filter(pk=card.pk).update(completed_at=cursor)

    # ------------------------------------------------------------------
    def seed_showcase(self, users, m, codes, pool):
        """The 10-line Bharat Switchgear order the mockup is drawn from.

        Everything interesting about the new shape lives here: three job cards
        over three different subsets of one order, one of them an urgent split,
        and five items still off the floor for five different reasons.
        """
        meera, deepak, sunil = users['meera'], users['deepak'], users['sunil']
        floor, prod = users['ramesh'], users['sunil']
        operators = users['operators']
        stations = list(ProcessStation.objects.filter(is_active=True).order_by('sequence'))

        booked_on = ago(SHOWCASE_BOOKED_AGO)
        quotation = Quotation.objects.create(
            customer=m['customers'][SHOWCASE_CUSTOMER],
            salesperson=users['imran'],
            status=QuotationStatus.APPROVED,
            approved_by=users['vikram'],
            approved_at=at(booked_on - timedelta(days=5)),
            expected_delivery=day(SHOWCASE_DUE_IN),
            **self.quote_defaults(booked_on - timedelta(days=10)))
        for pos, (code, qty, _state) in enumerate(SHOWCASE_ITEMS, start=1):
            obj = codes[code]
            self.add_line(quotation, obj, qty, self.band_rate(obj, qty), pos)

        quotation.po_number = 'PO/2026/0912'
        quotation.po_date = booked_on - timedelta(days=1)
        quotation.po_file = 'po/PO-2026-0912.pdf'
        quotation.order_type = OrderType.NEW
        # No drawing_file: the PO is what is common to the order now, and the
        # drawings arrive per item code from Design.
        quotation.save()

        order = SalesOrder.mint_from_quotation(
            quotation, users['priya'],
            expected_delivery=day(SHOWCASE_DUE_IN), designer=meera)
        order.booked_on = booked_on
        order.priority = Priority.P2
        order.stage_note = 'Split across 3 job cards — 5 lines still blocked'
        order.design_note = 'Bharat Switchgear panel set — 10 item codes'
        order.save(update_fields=['booked_on', 'priority', 'stage_note',
                                  'design_note', 'updated_at'])

        items = list(order.items.all())
        by_state = {}
        for item, (code, _qty, state) in zip(items, SHOWCASE_ITEMS):
            by_state.setdefault(state, []).append(item)

            if state != 'no_drawing':
                DrawingRevision.objects.create(
                    so_item=item, revision=0, source='Customer',
                    file=f'drawings/{code}-customer.pdf',
                    uploaded_by=order.salesperson, is_current=False,
                    note='Customer reference received with the PO')
                DrawingRevision.objects.create(
                    so_item=item, revision=1, source='Design',
                    file=f'drawings/{code}-rev01.pdf',
                    uploaded_by=meera, is_current=True,
                    note='Drawn by Design against the customer reference')
                item.holes = '4 x d4mm'
                item.drilling_detail = 'Corner holes, 6mm from edge'

            if state in {'dispatched', 'production', 'urgent',
                         'not_reserved', 'not_issued'}:
                item.design_started_at = at(booked_on + timedelta(days=1), 9)
                item.design_approved = True
                item.design_approved_at = at(booked_on + timedelta(days=3), 16)
                item.design_approved_by = meera
            elif state in {'not_approved', 'query'}:
                item.design_started_at = at(booked_on + timedelta(days=2), 11)
            item.save()

            if state in SHOWCASE_NEEDS_SHEET:
                sheet = self.take_sheet(pool, item.material, item.thickness)
                res = MaterialReservation.objects.create(
                    so_item=item, sheet=sheet, reserved_by=meera)
                if state != 'not_issued':
                    res.issue(deepak)
                if state == 'dispatched':
                    res.consume(sunil)

        # The one open query, raised against a single item so the other nine
        # lines keep their own blocked reasons.
        DesignQuery.raise_for(
            order, meera,
            'Warning sign: is the pictogram customer-supplied?',
            'The 250x180 warning sign artwork references an ISO pictogram we '
            'do not hold. Please confirm the customer is sending the vector, '
            'or we redraw it and re-quote the tooling.',
            so_item=by_state['query'][0])

        # ---- three cards over three different subsets ---------------------
        released = at(booked_on + timedelta(days=6), 11)
        op = 0

        done_card = self.run_card(
            by_state['dispatched'], stations, prod, floor, operators, op,
            released_at=released, priority=Priority.P2, take=None)
        op += done_card.done_count + 1
        self.backdate_card(done_card, booked_on)

        batch_card = self.run_card(
            by_state['production'], stations, prod, floor, operators, op,
            released_at=at(booked_on + timedelta(days=9), 10),
            priority=Priority.P2, take=6)
        op += batch_card.done_count + 1
        self.backdate_card(batch_card, booked_on + timedelta(days=3))

        urgent_card = self.run_card(
            by_state['urgent'], stations, prod, floor, operators, op,
            released_at=at(booked_on + timedelta(days=13), 15),
            priority=Priority.P1, take=5,
            is_urgent_split=True, urgent_reason=SHOWCASE_URGENT_REASON)
        self.backdate_card(urgent_card, booked_on + timedelta(days=7))

        # Package QR is stamped at packing, so only the finished lines carry one.
        for n, item in enumerate(by_state['dispatched']):
            item.package_qr = f'QR-{3311 + n}'
            item.save(update_fields=['package_qr', 'updated_at'])

        order.status = OrderStatus.IN_PRODUCTION
        order.save(update_fields=['status', 'updated_at'])
        return order

    # ------------------------------------------------------------------
    def seed_dispatch(self, users, orders):
        qr = 3320
        for idx, order, status, _ in orders:
            if status == OrderStatus.DISPATCHED:
                for item in order.items.all():
                    item.package_qr = f'QR-{qr}'
                    item.save(update_fields=['package_qr', 'updated_at'])
                    qr += 1
            if status == OrderStatus.DISPATCH_READY:
                DispatchRecord.objects.create(
                    order=order, ready_at=at(ago(2), 15),
                    remarks='QA cleared — 3 cartons, awaiting transporter.')
            elif status == OrderStatus.DISPATCHED:
                DispatchRecord.objects.create(
                    order=order, ready_at=at(ago(6), 12),
                    dispatched_at=at(ago(4), 17), dispatched_by=users['deepak'],
                    remarks='Handed to VRL Logistics, LR 4471.')

        # Hold and cancel run through the real transitions so the reasons,
        # the resume point and the material release are all recorded.
        by_idx = {idx: order for idx, order, _, _ in orders}
        for idx, reason in HOLD_REASONS.items():
            order = by_idx[idx]
            order.status = OrderStatus.IN_DESIGN
            order.save(update_fields=['status', 'updated_at'])
            order.put_on_hold(reason)
        for idx, reason in CANCEL_REASONS.items():
            by_idx[idx].cancel(reason)

    # ------------------------------------------------------------------
    def seed_live_quotations(self, users, m, codes):
        """The 25 minted quotations that are still in the sales pipeline."""
        for i, (cust, code, sales, status, qdate_ago, raw_qty) in enumerate(LIVE_QUOTES):
            qdate = ago(qdate_ago)
            code_obj = codes[code]
            qty = self.fit_qty(code_obj, raw_qty)
            q = Quotation.objects.create(
                customer=m['customers'][cust], salesperson=users[sales],
                status=status, enquiry_ref=f'ENQ/2026/{310 + i:04d}',
                expected_delivery=day(20 + i % 12),
                **self.quote_defaults(qdate))
            self.add_line(q, code_obj, qty, self.band_rate(code_obj, qty))

            if status != QuotationStatus.DRAFT:
                q.submitted_at = at(qdate + timedelta(days=1), 12)
            if status == QuotationStatus.REJECTED:
                q.approved_by = users['vikram']
                q.manager_comment = (
                    'Lead time promised is shorter than the tooling can support — '
                    'requote at 4 weeks.')
            elif status != QuotationStatus.DRAFT:
                q.approved_by = users['vikram']
                q.approved_at = at(qdate + timedelta(days=2), 15)
            if status in (QuotationStatus.SENT_TO_CUSTOMER, QuotationStatus.AWAITING_PO,
                          QuotationStatus.PO_UPLOADED, QuotationStatus.PO_REJECTED):
                q.sent_to_customer_at = at(qdate + timedelta(days=3), 10)
            if status in (QuotationStatus.PO_UPLOADED, QuotationStatus.PO_REJECTED):
                q.po_number = f'PO/2026/{800 + i:04d}'
                q.po_date = qdate + timedelta(days=4)
                q.po_file = f'po/PO-2026-{800 + i:04d}.pdf'
                # One PO_UPLOADED deliberately has no drawing yet.
                if not (status == QuotationStatus.PO_UPLOADED and code == 'MT-LBL-060'):
                    q.drawing_file = f'drawings/{code}-customer.pdf'
            if status == QuotationStatus.PO_REJECTED:
                q.po_rejection_reason = (
                    'PO value is ₹18,200 short of the quoted amount and the GSTIN '
                    'on the PO does not match the customer master.')
                q.po_rejected_by = users['priya']
                q.po_rejected_at = at(qdate + timedelta(days=5), 11)
            q.save()

    # ------------------------------------------------------------------
    def seed_reorder_levels(self, m):
        for mat, thk, minimum in REORDER_PLAN:
            ReorderLevel.objects.update_or_create(
                material=m['materials'][mat], thickness=m['thicknesses'][thk],
                defaults={'minimum_qty': minimum})

    def seed_alerts(self, users, quotes, orders):
        notes, logs = [], []
        for number in ('QTN-0417', 'QTN-0419', 'QTN-0421', 'QTN-0422'):
            q = quotes[number]
            notes.append(Notification(
                recipient=users['vikram'],
                message=f'{q.number} v{q.version} from {q.salesperson.short_name} '
                        f'awaits your approval',
                link='/sales/approvals/', object_ref=q.number))
            logs.append(AuditLog(
                actor=q.salesperson, object_type='Quotation', object_ref=q.number,
                action='Submitted for approval', to_value='PENDING_APPROVAL'))
        for query in DesignQuery.objects.filter(status=QueryStatus.OPEN):
            notes.append(Notification(
                recipient=query.assigned_to,
                message=f'Design query on {query.order.number}: {query.subject}',
                link='/design/queue/', object_ref=query.order.number))
        for idx, order, status, _ in orders:
            if status == OrderStatus.READY_TO_PRODUCE:
                notes.append(Notification(
                    recipient=users['sunil'],
                    message=f'{order.number} is ready to produce — material issued',
                    link='/production/', object_ref=order.number))
            logs.append(AuditLog(
                actor=users['priya'], object_type='SalesOrder',
                object_ref=order.number, action='Booked from PO',
                detail=order.quotation.po_number, to_value=str(status)))
        Notification.objects.bulk_create(notes)
        AuditLog.objects.bulk_create(logs)

    # ------------------------------------------------------------------
    def report(self):
        w = self.stdout.write
        line = '-' * 62

        w('')
        w(line)
        w('SEEDED DEMO DATA — TODAY is 10 Aug 2026')
        w(line)

        w(f'Customers         {Customer.objects.count()}')
        w(f'Item codes        {ItemCode.objects.count()}  '
          f'(active {ItemCode.objects.filter(is_active=True).count()}, '
          f'inactive {ItemCode.objects.filter(is_active=False).count()}, '
          f'pending {ItemCode.objects.filter(approval_state=ItemCodeApproval.PENDING).count()}, '
          f'customers covered '
          f'{ItemCode.objects.values("customer").distinct().count()})')

        w('')
        w('QUOTATIONS')
        live = Quotation.objects.exclude(status=QuotationStatus.CONVERTED) \
                                .filter(revisions__isnull=True)
        w(f'  rows {Quotation.objects.count()}   '
          f'converted {Quotation.objects.filter(status=QuotationStatus.CONVERTED).count()}   '
          f'live (latest version, not converted) {live.count()}')
        for value, label in QuotationStatus.choices:
            n = live.filter(status=value).count()
            if n:
                w(f'    {label:<34} {n}')
        pend = Quotation.objects.filter(status=QuotationStatus.PENDING_APPROVAL)
        days = [q.days_pending for q in pend if q.days_pending is not None]
        w(f'    approval queue: {pend.count()} pending, '
          f'{sum(1 for q in pend if q.version > 1)} resubmitted (v2+), '
          f'{sum(1 for q in pend if q.unapproved_items)} carrying unapproved codes, '
          f'{sum(1 for q in pend if q.grand_total > 50000)} high value, '
          f'avg {sum(days) / len(days):.1f}d pending' if days else '')

        w('')
        w('SALES ORDERS')
        counts = Counter(SalesOrder.objects.values_list('status', flat=True))
        for value, label in OrderStatus.choices:
            if counts.get(value):
                w(f'    {label:<26} {counts[value]}')
        health = Counter(o.health for o in SalesOrder.objects.all())
        open_orders = SalesOrder.objects.exclude(
            status__in=[OrderStatus.CANCELLED, OrderStatus.DISPATCHED]).count()
        w(f'    open orders {open_orders}   on track {health[Health.GREEN]}   '
          f'held {health[Health.AMBER]}   delayed {health[Health.RED]}   '
          f'closed/gray {health[Health.GRAY]}')
        w(f'    booked today (10 Aug) '
          f'{SalesOrder.objects.filter(booked_on=TODAY).count()}')

        w('')
        w('STOCK')
        s = Counter(Sheet.objects.values_list('status', flat=True))
        w(f'    total {Sheet.objects.count()}   available {s[SheetStatus.AVAILABLE]}   '
          f'reserved {s[SheetStatus.RESERVED]}   issued {s[SheetStatus.ISSUED]}   '
          f'consumed {s[SheetStatus.CONSUMED]}   '
          f'offcuts {Sheet.objects.filter(is_offcut=True).count()}')
        low = []
        for level in ReorderLevel.objects.select_related('material', 'thickness'):
            n = Sheet.objects.filter(material=level.material, thickness=level.thickness,
                                     status=SheetStatus.AVAILABLE).count()
            if n < level.minimum_qty:
                low.append(f'{level.material.name} {level.thickness.name} '
                           f'({n}/{level.minimum_qty})')
        w(f'    below reorder level {len(low)}: {", ".join(low)}')

        w('')
        w('PRODUCTION & DISPATCH')
        cards = JobCard.objects.all()
        w(f'    job cards {cards.count()}   '
          f'complete {sum(1 for c in cards if c.is_complete)}   '
          f'in progress {sum(1 for c in cards if not c.is_complete)}')
        w(f'    card lines {JobCardItem.objects.count()}   '
          f'multi-item cards {sum(1 for c in cards if c.item_count > 1)}   '
          f'urgent splits {cards.filter(is_urgent_split=True).count()}')
        w(f'    stations {JobCardStation.objects.count()}   '
          f'done {JobCardStation.objects.filter(status=StationStatus.DONE).count()}   '
          f'skipped {JobCardStation.objects.filter(status=StationStatus.SKIPPED).count()}')
        w(f'    dispatch records {DispatchRecord.objects.count()}  '
          f'(awaiting outward '
          f'{DispatchRecord.objects.filter(dispatched_at__isnull=True).count()}, '
          f'dispatched '
          f'{DispatchRecord.objects.filter(dispatched_at__isnull=False).count()})')
        w(f'    design queries {DesignQuery.objects.count()}  '
          f'(open {DesignQuery.objects.filter(status=QueryStatus.OPEN).count()}, '
          f'closed {DesignQuery.objects.filter(status=QueryStatus.CLOSED).count()})')

        showcase = (SalesOrder.objects.annotate(n=Count('items'))
                    .order_by('-n', 'number').first())
        if showcase and showcase.n > 1:
            w('')
            w(f'SHOWCASE ORDER {showcase.number} — {showcase.n} item codes, '
              f'{showcase.get_status_display()}')
            seen = {}
            for item in showcase.items.all():
                card = item.current_job_card
                if card:
                    seen[card.number] = card
                w(f'    #{item.position:>2} {str(item.item_code or ""):<12} '
                  f'{item.item_status_label:<30} {item.blocked_reason or "—"}')
            for number, card in sorted(seen.items()):
                w(f'    {number}  {card.item_count} item(s)  '
                  f'{card.progress_percent}% done'
                  f'{"  URGENT SPLIT" if card.is_urgent_split else ""}')
            w(f'    drawings still owed by Design: '
              f'{len(showcase.items_missing_drawing)}')

        w('')
        w(f'Users {User.objects.count()}  '
          f'(operators {User.objects.filter(is_operator=True).count()})  '
          f'— password: demo1234')
        w(line)
