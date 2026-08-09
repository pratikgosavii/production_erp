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


class QueryStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    ANSWERED = 'ANSWERED', 'Answered'
    CLOSED = 'CLOSED', 'Closed'


# Number prefixes, one registry so nothing collides (§9 Numbering).
PREFIX_QUOTATION = 'QTN'
PREFIX_SALES_ORDER = 'SO'
PREFIX_JOB_CARD = 'JC'
PREFIX_SHEET = 'SHT'
PREFIX_OFFCUT = 'OFF'
