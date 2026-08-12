"""Sales Order — minted by Accounts when they confirm the PO (§1.1, §3.4).

The SO keeps a reference back to its originating quotation for the rest of its
life: that link is how a design query finds the exact salesperson who raised
the quote (§7.1), and how pricing is traced.
"""
from django.conf import settings
from django.db import models, transaction

from core.constants import (
    PREFIX_SALES_ORDER, Health, OrderStatus, OrderType, Priority, Stage,
)
from core.models import Sequence, TimeStampedModel
from core.services import health_for, stage_for
from masters.models import Customer
from sales.models import Quotation, QuotationItem


class SalesOrder(TimeStampedModel):
    """1 quotation = 1 SO, carrying all its line items (§10.1·1)."""

    number = models.CharField(max_length=20, unique=True, editable=False)
    quotation = models.ForeignKey(
        Quotation, on_delete=models.PROTECT, related_name='sales_orders',
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='orders')

    status = models.CharField(
        max_length=25, choices=OrderStatus.choices, default=OrderStatus.BOOKED,
    )
    order_type = models.CharField(
        max_length=10, choices=OrderType.choices, default=OrderType.NEW,
    )

    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='booked_orders',
    )
    booked_on = models.DateField(null=True, blank=True)
    expected_delivery = models.DateField(null=True, blank=True)

    assigned_designer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='design_jobs',
    )
    design_note = models.CharField(max_length=250, blank=True)

    # Free-text detail appended to the stage name on the booking dashboard,
    # e.g. "Etching in progress". Cannot be derived, so whoever advances the
    # stage writes it.
    stage_note = models.CharField(max_length=120, blank=True)

    # Set by the Production Manager on the ready queue, BEFORE a job card
    # exists; copied onto the JobCard when it is minted.
    priority = models.PositiveSmallIntegerField(
        choices=Priority.choices, default=Priority.P3,
    )

    # Hand-dragged position WITHIN a priority column on the Production
    # Priority Board. Lower sorts earlier; 0 is simply "not yet arranged", so a
    # freshly booked order drops to the top of its column and the planner can
    # push it down. Priority answers "how urgent"; queue_rank answers "in what
    # order among the equally urgent", which priority alone cannot express.
    queue_rank = models.IntegerField(default=0)

    # §10.1·5 — hold is resumable, cancel is terminal; both need a reason.
    hold_reason = models.TextField(blank=True)
    status_before_hold = models.CharField(max_length=25, blank=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} — {self.customer.name}'

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number(PREFIX_SALES_ORDER, start=1030)
        super().save(*args, **kwargs)

    # ---- derived, so every screen agrees --------------------------------
    @property
    def stage(self):
        return stage_for(self.status)

    @property
    def stage_label(self):
        return Stage(self.stage).label

    @property
    def health(self):
        return health_for(self)

    @property
    def health_label(self):
        return Health(self.health).label

    @property
    def health_reason(self):
        """The qualifier after the badge, e.g. 'Held — design query'.

        Mirrors the branch order in health_for() so the two never disagree.
        """
        if self.status == OrderStatus.CANCELLED:
            return 'Cancelled'
        if self.status == OrderStatus.DISPATCHED:
            return 'Delivered'
        if self.status == OrderStatus.ON_HOLD:
            return f'Held — {self.hold_reason}' if self.hold_reason else 'Held'
        if self.status == OrderStatus.DESIGN_QUERY or self.has_open_query:
            return 'Held — design query'
        if not self.drawing_available:
            return 'Awaiting drawing'
        if self.health == Health.RED:
            return 'Delayed'
        return 'On track'

    @property
    def salesperson(self):
        """Query routing target (§7.1) — the person who raised the quote."""
        return self.quotation.salesperson

    @property
    def order_value(self):
        return self.quotation.grand_total

    # ---- flags the health/gate rules read -------------------------------
    @property
    def drawing_available(self):
        """§10.2·1 — one drawing per item code, uploaded by DESIGN.

        Sales no longer supply the drawing; the PO is what is common to the
        order. So the order counts as drawn only when EVERY item has a current
        revision. `Quotation.drawing_file` may still hold a customer-supplied
        reference, but it is deliberately not consulted here.
        """
        items = list(self.items.all())
        return bool(items) and all(i.current_drawing is not None for i in items)

    @property
    def items_missing_drawing(self):
        """The item codes Design still owes a drawing for."""
        return [i for i in self.items.all() if i.current_drawing is None]

    @property
    def has_open_query(self):
        from core.constants import QueryStatus
        return self.queries.exclude(status=QueryStatus.CLOSED).exists()

    @property
    def design_approved(self):
        items = self.items.all()
        return bool(items) and all(i.design_approved for i in items)

    @property
    def material_issued(self):
        items = self.items.all()
        return bool(items) and all(i.material_issued for i in items)

    @property
    def is_ready_to_produce(self):
        return self.design_approved and self.material_issued

    # ---- transitions ----------------------------------------------------
    @classmethod
    @transaction.atomic
    def mint_from_quotation(cls, quotation, user, expected_delivery=None, designer=None):
        """Accounts confirm the PO -> the SO number is stamped here (§1.1)."""
        from django.utils import timezone
        from core.constants import QuotationStatus

        order = cls.objects.create(
            quotation=quotation,
            customer=quotation.customer,
            order_type=quotation.order_type,
            booked_by=user,
            booked_on=timezone.now().date(),
            expected_delivery=expected_delivery or quotation.expected_delivery,
            assigned_designer=designer,
            status=OrderStatus.IN_DESIGN,
        )
        for qi in quotation.items.all():
            SOItem.objects.create(order=order, quotation_item=qi, position=qi.position)

        quotation.status = QuotationStatus.CONVERTED
        quotation.save(update_fields=['status', 'updated_at'])
        return order

    def put_on_hold(self, reason):
        if self.status in OrderStatus.terminal():
            raise ValueError('A dispatched or cancelled order cannot be held.')
        self.status_before_hold = self.status
        self.status = OrderStatus.ON_HOLD
        self.hold_reason = reason
        self.save(update_fields=['status', 'status_before_hold', 'hold_reason', 'updated_at'])

    def resume(self):
        if self.status != OrderStatus.ON_HOLD:
            return
        self.status = self.status_before_hold or OrderStatus.BOOKED
        self.status_before_hold = ''
        self.hold_reason = ''
        self.save(update_fields=['status', 'status_before_hold', 'hold_reason', 'updated_at'])

    @transaction.atomic
    def cancel(self, reason):
        """Terminal. Reserved material goes back to available stock (§10.1·5)."""
        from core.constants import SheetStatus

        for item in self.items.all():
            for res in item.reservations.filter(released_at__isnull=True):
                res.release()
        self.status = OrderStatus.CANCELLED
        self.cancel_reason = reason
        self.save(update_fields=['status', 'cancel_reason', 'updated_at'])

    def refresh_status(self):
        """Recompute the pipeline position after a design/material/production event."""
        if self.status in {OrderStatus.ON_HOLD, OrderStatus.CANCELLED}:
            return

        items = list(self.items.all())
        # A card may cover several items (and several orders); walk the
        # junction rows and de-duplicate by card.
        cards, carded_items = {}, 0
        for item in items:
            links = list(item.job_card_items.all())
            if links:
                carded_items += 1
            for link in links:
                cards[link.job_card_id] = link.job_card

        if cards:
            every_item_carded = carded_items == len(items)
            all_complete = all(c.is_complete for c in cards.values())
            if every_item_carded and all_complete:
                self.status = OrderStatus.DISPATCH_READY
            else:
                self.status = OrderStatus.IN_PRODUCTION
        elif self.is_ready_to_produce:
            self.status = OrderStatus.READY_TO_PRODUCE
        elif self.design_approved:
            self.status = OrderStatus.DESIGN_APPROVED
        elif self.has_open_query:
            self.status = OrderStatus.DESIGN_QUERY
        else:
            self.status = OrderStatus.IN_DESIGN
        self.save(update_fields=['status', 'updated_at'])


class SOItem(TimeStampedModel):
    """One item of a sales order — its own design job, job card and material."""

    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='items')
    quotation_item = models.ForeignKey(
        QuotationItem, on_delete=models.PROTECT, related_name='so_items',
    )
    position = models.PositiveSmallIntegerField(default=1)

    # §10.2·4 — holes/drilling are entered by the Designer, not by Sales.
    holes = models.CharField(max_length=120, blank=True)
    drilling_detail = models.CharField(max_length=200, blank=True)

    # Distinguishes "Not started" from "In progress" on the Design queue.
    design_started_at = models.DateTimeField(null=True, blank=True)
    design_approved = models.BooleanField(default=False)
    design_approved_at = models.DateTimeField(null=True, blank=True)
    design_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_designs',
    )

    # Printed on the carton at packing; shown per item on the SO detail screen
    # so a dispatched line can be traced back to a physical package.
    package_qr = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f'{self.order.number} #{self.position}'

    @property
    def item_code(self):
        return self.quotation_item.item_code

    @property
    def material(self):
        return self.quotation_item.material

    @property
    def thickness(self):
        return self.quotation_item.thickness

    @property
    def quantity(self):
        return self.quotation_item.quantity

    @property
    def current_drawing(self):
        return self.drawing_revisions.filter(is_current=True).first()

    @property
    def material_reserved(self):
        return self.reservations.filter(released_at__isnull=True).exists()

    @property
    def material_issued(self):
        """The production gate depends on ISSUED, not merely reserved (§10.1·3)."""
        return self.reservations.filter(
            released_at__isnull=True, issued_at__isnull=False,
        ).exists()

    # ---- job card + per-item status -------------------------------------
    @property
    def current_job_card(self):
        """The card this item is on — the latest one, if it has been re-run."""
        link = (
            self.job_card_items
            .select_related('job_card')
            .order_by('-created_at', '-id')
            .first()
        )
        return link.job_card if link else None

    @property
    def is_ready(self):
        """§4 gate at item level — drawing approved AND material issued."""
        return bool(self.design_approved and self.material_issued)

    @property
    def blocked_reason(self):
        """Why this item is not on the floor yet — '' once nothing is in the way.

        The branch order is the escalation order a planner would read down:
        the whole order first, then design, then material, then the card.
        """
        from core.constants import OrderStatus, QueryStatus

        order = self.order
        if order.status == OrderStatus.ON_HOLD:
            return 'Order on hold'
        if order.status == OrderStatus.CANCELLED:
            return 'Order cancelled'

        open_on_item = self.queries.exclude(status=QueryStatus.CLOSED).exists()
        # An order-level query (one raised without naming an item) blocks
        # every item; an item-level query blocks only its own item.
        open_on_order = order.queries.filter(so_item__isnull=True) \
                                     .exclude(status=QueryStatus.CLOSED).exists()
        if open_on_item or open_on_order:
            return 'Design query open'

        if self.current_drawing is None:
            return 'Awaiting drawing from Design'
        if not self.design_approved:
            return 'Design not approved'
        if not self.material_reserved:
            return 'Material not reserved'
        if not self.material_issued:
            return 'Material not issued by Stores'

        card = self.current_job_card
        if card:
            return f'On job card {card.number}'
        return ''

    @property
    def item_status_label(self):
        """One short phrase for the SO detail table."""
        from core.constants import OrderStatus

        card = self.current_job_card
        if self.order.status == OrderStatus.DISPATCHED or (
            self.package_qr and card and card.is_complete
        ):
            return 'Dispatched'
        if card:
            return 'In production'
        if self.is_ready:
            return 'Ready'
        return self.blocked_reason or 'Ready'
