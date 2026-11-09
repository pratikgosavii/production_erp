"""Canonical vocabulary for the whole ERP.

Everything that drives a badge, a gate or a handoff lives here so the rules are
stated once. See PROJECT_DESIGN.md §1 (state machine), §4 (derived states) and
§10.2·10 (stage vs status).
"""
from django.db import models


class QuotationStatus(models.TextChoices):
    """Life of a quotation, before any Sales Order exists (§1.1)."""

    DRAFT = 'DRAFT', 'Draft'
    PENDING_APPROVAL = 'PENDING_APPROVAL', 'Pending Approval'
    REJECTED = 'REJECTED', 'Rejected — needs revision'
    APPROVED = 'APPROVED', 'Approved'
    SENT_TO_CUSTOMER = 'SENT_TO_CUSTOMER', 'Sent to Customer'
    AWAITING_PO = 'AWAITING_PO', 'Awaiting PO'
    PO_UPLOADED = 'PO_UPLOADED', 'PO Uploaded'
    PO_REJECTED = 'PO_REJECTED', 'PO Rejected by Accounts'
    CONVERTED = 'CONVERTED', 'Converted to Sales Order'

    @classmethod
    def editable(cls):
        """States where the salesperson may still change the quote."""
        return {cls.DRAFT, cls.REJECTED}


class OrderStatus(models.TextChoices):
    """Life of a Sales Order, from PO confirmation to dispatch (§1)."""

    BOOKED = 'BOOKED', 'Booked'
    IN_DESIGN = 'IN_DESIGN', 'In Design'
    DESIGN_QUERY = 'DESIGN_QUERY', 'Design Query Raised'
    DESIGN_APPROVED = 'DESIGN_APPROVED', 'Design Approved'
    MATERIAL_RESERVED = 'MATERIAL_RESERVED', 'Material Reserved'
    MATERIAL_ISSUED = 'MATERIAL_ISSUED', 'Material Issued'
    READY_TO_PRODUCE = 'READY_TO_PRODUCE', 'Ready to Produce'
    IN_PRODUCTION = 'IN_PRODUCTION', 'In Production'
    PRODUCTION_COMPLETED = 'PRODUCTION_COMPLETED', 'Production Completed'
    DISPATCH_READY = 'DISPATCH_READY', 'Ready for Dispatch'
    DISPATCHED = 'DISPATCHED', 'Dispatched'
    ON_HOLD = 'ON_HOLD', 'On Hold'
    CANCELLED = 'CANCELLED', 'Cancelled'

    @classmethod
    def pre_dispatch(cls):
        """§10.1·5 — Hold/Cancel are offered from any pre-dispatch state."""
        return {
            cls.BOOKED, cls.IN_DESIGN, cls.DESIGN_QUERY, cls.DESIGN_APPROVED,
            cls.MATERIAL_RESERVED, cls.MATERIAL_ISSUED, cls.READY_TO_PRODUCE,
            cls.IN_PRODUCTION, cls.PRODUCTION_COMPLETED,
        }

    @classmethod
    def terminal(cls):
        return {cls.DISPATCHED, cls.CANCELLED}


class Stage(models.IntegerChoices):
    """The 7-dot position track (§10.2·10).

    Stage tracks WHERE the job is. It is deliberately independent of Health,
    which tracks how well it is going.
    """

    PO_INTAKE = 1, 'PO Intake'
    DESIGN = 2, 'Design'
    MATERIAL = 3, 'Material / Nesting'
    PRODUCTION = 4, 'Production'
    QC = 5, 'QC'
    DISPATCH = 6, 'Dispatch'
    DELIVERED = 7, 'Delivered'


class Health(models.TextChoices):
    """The status badge colour (§10.2·10). Derived, never typed."""

    GREEN = 'GREEN', 'On track'
    AMBER = 'AMBER', 'Held'
    RED = 'RED', 'Delayed'
    GRAY = 'GRAY', 'Not started / Delivered'


# Which stage each order status sits at. Single source of truth for the
# 7-dot track so every screen draws the same thing.
STATUS_STAGE = {
    OrderStatus.BOOKED: Stage.PO_INTAKE,
    OrderStatus.IN_DESIGN: Stage.DESIGN,
    OrderStatus.DESIGN_QUERY: Stage.DESIGN,
    OrderStatus.DESIGN_APPROVED: Stage.DESIGN,
    OrderStatus.MATERIAL_RESERVED: Stage.MATERIAL,
    OrderStatus.MATERIAL_ISSUED: Stage.MATERIAL,
    OrderStatus.READY_TO_PRODUCE: Stage.MATERIAL,
    OrderStatus.IN_PRODUCTION: Stage.PRODUCTION,
    OrderStatus.PRODUCTION_COMPLETED: Stage.QC,
    OrderStatus.DISPATCH_READY: Stage.DISPATCH,
    OrderStatus.DISPATCHED: Stage.DELIVERED,
}


class SheetStatus(models.TextChoices):
    """Material lifecycle (§10.1·3 — Reserved and Issued are two steps)."""

    AVAILABLE = 'AVAILABLE', 'Available'
    RESERVED = 'RESERVED', 'Reserved'      # set by Design
    ISSUED = 'ISSUED', 'Issued'            # set by Stores — this is the production gate
    CONSUMED = 'CONSUMED', 'Consumed'
    LEFTOVER = 'LEFTOVER', 'Leftover (offcut)'
    SCRAP = 'SCRAP', 'Scrap'


class ItemCodeApproval(models.TextChoices):
    """An item code's rate is only trusted once a Sales Manager signs it off."""

    PENDING = 'PENDING', 'Pending Approval'
    APPROVED = 'APPROVED', 'Approved'


class Priority(models.IntegerChoices):
    """§4 — numeric 1-4. High/Normal/Low was explicitly dropped."""

    P1 = 1, '1 — Highest'
    P2 = 2, '2 — High'
    P3 = 3, '3 — Normal'
    P4 = 4, '4 — Low'


class OrderType(models.TextChoices):
    NEW = 'NEW', 'New'
    REPEAT = 'REPEAT', 'Repeat'


class Process(models.TextChoices):
    """The three things Ravi Raj actually does to a sheet.

    Design treats them differently and that is the point of carrying the field:
    etching needs a visual proof on every job, milling needs etch-factor
    compensation on the artwork, and laser needs kerf compensation plus a
    minimum feature check before the DXF goes to the machine.

    Everything on the system before this field existed was chemical etching,
    which is why that is the default.
    """

    ETCHING = 'ETCHING', 'Chemical Etching'
    MILLING = 'MILLING', 'Chemical Milling'
    LASER = 'LASER', 'Laser Cutting'


#: Short labels and the mark used on the design queue, per process.
PROCESS_UI = {
    Process.ETCHING: {'short': 'Chem Etching', 'mark': '▦',
                      'blurb': 'Nameplates, grilles - visual proof needed'},
    Process.MILLING: {'short': 'Chem Milling', 'mark': '◈',
                      'blurb': 'Precision parts - etch-factor compensation'},
    Process.LASER: {'short': 'Laser Cut', 'mark': '◉',
                    'blurb': 'DXF to machine - kerf compensation'},
}


#: The six checks a designer ticks before a job may go to the customer.
#: Keyed so a tick survives re-wording of the label; the key is what is stored.
#: Ordered - the card renders them in this order and counts "n of 6" from it.
PROCESS_CHECKS = {
    Process.ETCHING: [
        ('proofread', 'Text proofread against the customer drawing',
         'Wrong text is the most common reason proofs come back'),
        ('size', 'Overall size matches the spec',
         'Check the item row above, not your memory'),
        ('layers', 'Half-etch and through-etch on separate layers',
         'Mixed layers means the etch station cannot read depth'),
        ('fill', 'Fill colours on the correct layer',
         'Wrong layer means the fill station gets no separation'),
        ('weights', 'Etch line weights at or above shop minimum',
         'Thinner lines will not hold after etching'),
        ('saved', 'File saved in the job folder',
         'Saved elsewhere means nobody downstream can find it'),
    ],
    Process.MILLING: [
        ('critdims', 'Critical dimensions verified against customer drawing',
         'Milling is a tolerance job - appearance is not the test'),
        ('etchfactor', 'Etch-factor compensation applied',
         'Undercut is roughly 1.5 x thickness per side - without it every part is undersize'),
        ('tolerance', 'Stated tolerance confirmed achievable',
         'If the tolerance is not holdable on this thickness, say so now, not after etching'),
        ('tabs', 'Holding tabs placed',
         'Parts without tabs drop into the bath and are lost'),
        ('registration', 'Front/back artwork registered',
         'Two-sided etch misaligns if registration marks are missing'),
        ('saved', 'File saved in the job folder',
         'Saved elsewhere means nobody downstream can find it'),
    ],
    Process.LASER: [
        ('dims', 'Dimensions verified against customer drawing',
         'Check the drawing, not an earlier DXF'),
        ('kerf', 'Kerf compensation applied',
         'Without it every cut part comes out undersize by the kerf'),
        ('minfeature', 'Minimum feature size checked - hole diameter >= material thickness',
         'Holes smaller than thickness will not cut clean'),
        ('leadin', 'Lead-in / lead-out placed off the finished edge',
         'A lead-in on the visible edge leaves a witness mark'),
        ('microjoints', 'Micro-joints placed so parts do not drop',
         'Parts dropping into the bed get scratched or lost'),
        ('exported', 'DXF exported to the job folder',
         'The operator loads from this folder only'),
    ],
}

#: What the same gate is called, per process. Etching sends a visual proof;
#: milling sends a dimensional sheet; laser sends a DXF for confirmation.
PROCESS_CARD = {
    Process.ETCHING: {
        'file_label': 'Design File',
        'critical_label': 'Fill / finish',
        'gate3': 'Visual proof approval',
        'proof_title': 'Customer Visual Proof - manual hand-off',
        'download': 'Download Proof PDF',
        'template': 'etching template',
        'template_note': 'The etching template carries the correct layers, fill-colour '
                         'separation and the title block. Never start from a blank page '
                         'or an old job file.',
    },
    Process.MILLING: {
        'file_label': 'Design File',
        'critical_label': 'Tolerance',
        'gate3': 'Dimensional approval',
        'proof_title': 'Customer Dimensional Approval - manual hand-off',
        'download': 'Download Dimensional Sheet',
        'template': 'milling template',
        'template_note': 'The milling template carries the etch-factor layer, '
                         'critical-dimension callouts and tab positions. Never start '
                         'from a blank page or an old job file.',
    },
    Process.LASER: {
        'file_label': 'Machine File',
        'critical_label': 'Min feature',
        'gate3': 'DXF confirmation',
        'proof_title': 'Customer DXF Confirmation - manual hand-off',
        'download': 'Download DXF + PDF',
        'template': 'laser template',
        'template_note': 'The laser template carries the cut/engrave layer split and '
                         'lead-in defaults. Export the DXF into the machine folder - do '
                         'not hand a .cdr to the operator.',
    },
}

#: Where a job's files live. One place, derived - never typed by hand, because
#: a file saved somewhere else is a file nobody downstream can find.
JOB_FOLDER_ROOT = r'D:\Jobs'


class Currency(models.TextChoices):
    INR = 'INR', 'INR (Rs.)'
    USD = 'USD', 'USD ($)'
    EUR = 'EUR', 'EUR (E)'


class ApprovalSource(models.TextChoices):
    """How an item code's rate got approved — keeps approved_via a clean QTN."""

    QUOTATION = 'QUOTATION', 'Via quotation'
    ADMIN = 'ADMIN', 'Added directly by Admin'


class StationStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    DONE = 'DONE', 'Done'
    SKIPPED = 'SKIPPED', 'Skipped'


class RejectionReason(models.TextChoices):
    """Why pieces were rejected at a station.

    A fixed list rather than free text so rejections are countable — "how much
    under-etch did we scrap last month" is the question ISO asks, and typed
    notes cannot answer it.
    """

    UNDER_ETCH = 'UNDER_ETCH', 'Under-etch'
    OVER_ETCH = 'OVER_ETCH', 'Over-etch'
    EDGE_DEFECT = 'EDGE_DEFECT', 'Edge defect'
    COLOUR_BLEED = 'COLOUR_BLEED', 'Colour bleed'
    DIMENSION_MISMATCH = 'DIMENSION_MISMATCH', 'Dimension mismatch'
    HANDLING_DAMAGE = 'HANDLING_DAMAGE', 'Handling damage'
    OTHER = 'OTHER', 'Other'


class Disposition(models.TextChoices):
    """What happens to the rejected pieces."""

    SCRAP = 'SCRAP', 'Scrap'
    REWORK = 'REWORK', 'Rework'
    CONCESSION = 'CONCESSION', 'Use as is (concession)'


class ProofOutcome(models.TextChoices):
    """How a customer answered an artwork proof round."""

    SENT = 'SENT', 'Sent - awaiting customer'
    APPROVED = 'APPROVED', 'Approved by customer'
    CHANGES = 'CHANGES', 'Changes requested'


class QueryStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    ANSWERED = 'ANSWERED', 'Answered'
    CLOSED = 'CLOSED', 'Closed'


# Number prefixes, one registry so nothing collides (§9 Numbering).
PREFIX_QUOTATION = 'QTN'
PREFIX_NCR = 'NCR'
PREFIX_SALES_ORDER = 'SO'
PREFIX_JOB_CARD = 'JC'
PREFIX_SHEET = 'SHT'
PREFIX_OFFCUT = 'OFF'


class NCRSource(models.TextChoices):
    """Where a non-conformance came from. One record, five doors in."""

    PRODUCTION = 'PRODUCTION', 'Production rejection'
    QC = 'QC', 'QC inspection failure'
    #: Rework traced back to the drawing rather than the shop floor. Design's
    #: scorecard counts these, so without the door there is nothing to count.
    DESIGN = 'DESIGN', 'Design error'
    CUSTOMER = 'CUSTOMER', 'Customer complaint'
    SUPPLIER = 'SUPPLIER', 'Supplier non-conformance'
    AUDIT = 'AUDIT', 'Audit finding'


class NCRStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    INVESTIGATING = 'INVESTIGATING', 'Investigating'
    ACTION_TAKEN = 'ACTION_TAKEN', 'Action taken'
    CLOSED = 'CLOSED', 'Closed'


class Severity(models.TextChoices):
    MINOR = 'MINOR', 'Minor'
    MAJOR = 'MAJOR', 'Major'
    CRITICAL = 'CRITICAL', 'Critical'
