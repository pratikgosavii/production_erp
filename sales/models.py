"""Quotation, its line items and their batch-wise price tiers (§3.2, §3.3).

A quotation lives under its QTN only. No Sales Order exists until Accounts
confirm the PO (§1.1) — that is why the PO fields hang off this model rather
than off SalesOrder.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction

from core.constants import (
    PREFIX_QUOTATION, Currency, ItemCodeApproval, OrderType, QuotationStatus,
)
from core.models import Sequence, TimeStampedModel
from masters.models import Customer, Finish, ItemCode, Material, Thickness


class Quotation(TimeStampedModel):
    """One quotation = one customer, many line items, one eventual SO (§10.1·1)."""

    # Not unique on its own: a revision deliberately reuses the number and
    # bumps the version, so uniqueness is (number, version) — see Meta.
    number = models.CharField(max_length=20, editable=False, db_index=True)
    version = models.PositiveSmallIntegerField(default=1)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.INR)
    # A rejected quote is revised as v2 rather than edited in place, so the
    # manager's original comment and the numbers it referred to survive.
    supersedes = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='revisions',
    )

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='quotations')
    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='quotations',
    )

    status = models.CharField(
        max_length=20, choices=QuotationStatus.choices, default=QuotationStatus.DRAFT,
    )

    quote_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    enquiry_ref = models.CharField(max_length=40, blank=True)

    payment_terms = models.CharField(max_length=200, blank=True)
    delivery_terms = models.CharField(max_length=60, blank=True)
    lead_time = models.CharField(max_length=120, blank=True)

    # Charges + tax, entered on the form (§3.2 totals block)
    tooling_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    packing_forwarding = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    freight = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'))

    # Submitted stamp drives the "3d pending" column on the approval queue.
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Manager approval (§3.3b)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_quotations',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    manager_comment = models.TextField(blank=True)

    sent_to_customer_at = models.DateTimeField(null=True, blank=True)

    # PO capture (§3.3 — this screen is the origin of these fields)
    po_number = models.CharField(max_length=60, blank=True)
    po_file = models.FileField(upload_to='po/', blank=True)
    po_date = models.DateField(null=True, blank=True)
    drawing_file = models.FileField(upload_to='drawings/', blank=True)
    expected_delivery = models.DateField(null=True, blank=True)
    order_type = models.CharField(
        max_length=10, choices=OrderType.choices, default=OrderType.NEW,
    )
    po_rejection_reason = models.TextField(blank=True)
    po_rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='po_rejections',
    )
    po_rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['number', 'version'], name='uniq_quotation_number_version',
            ),
        ]

    def __str__(self):
        return f'{self.number} v{self.version} — {self.customer.name}'

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number(PREFIX_QUOTATION, start=400)
        super().save(*args, **kwargs)

    # ---- money ----------------------------------------------------------
    @property
    def sub_total(self):
        return sum((i.line_amount for i in self.items.all()), Decimal('0.00'))

    @property
    def charges_total(self):
        return self.tooling_charges + self.packing_forwarding + self.freight

    @property
    def taxable_amount(self):
        return self.sub_total + self.charges_total

    @property
    def gst_amount(self):
        return (self.taxable_amount * self.gst_percent / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def grand_total(self):
        return self.taxable_amount + self.gst_amount

    # ---- workflow -------------------------------------------------------
    @property
    def has_drawing(self):
        return bool(self.drawing_file)

    @property
    def unapproved_items(self):
        """Line items whose item code still needs the manager's sign-off."""
        return [i for i in self.items.select_related('item_code') if i.needs_code_approval]

    @property
    def days_pending(self):
        """Age of the approval request, in days. Blank until submitted."""
        if not self.submitted_at:
            return None
        from django.utils import timezone
        return (timezone.now() - self.submitted_at).days

    def submit(self):
        from django.utils import timezone
        self.status = QuotationStatus.PENDING_APPROVAL
        self.submitted_at = timezone.now()
        self.save(update_fields=['status', 'submitted_at', 'updated_at'])

    @transaction.atomic
    def approve(self, user):
        """Approving the quote also locks in every new/revised item-code rate."""
        from django.utils import timezone

        for item in self.items.select_related('item_code'):
            if item.item_code and item.needs_code_approval:
                item.item_code.approve(item.effective_rate, user, self.number)

        self.status = QuotationStatus.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

    def reject(self, user, comment):
        self.status = QuotationStatus.REJECTED
        self.approved_by = user
        self.manager_comment = comment
        self.save(update_fields=['status', 'approved_by', 'manager_comment', 'updated_at'])

    @transaction.atomic
    def create_revision(self):
        """Rejected -> revise & resubmit as the next version (§3.3)."""
        new = Quotation.objects.create(
            version=self.version + 1,
            supersedes=self,
            customer=self.customer,
            salesperson=self.salesperson,
            quote_date=self.quote_date,
            valid_until=self.valid_until,
            enquiry_ref=self.enquiry_ref,
            payment_terms=self.payment_terms,
            delivery_terms=self.delivery_terms,
            lead_time=self.lead_time,
            tooling_charges=self.tooling_charges,
            packing_forwarding=self.packing_forwarding,
            freight=self.freight,
            gst_percent=self.gst_percent,
            status=QuotationStatus.DRAFT,
        )
        # Carry the number forward — a revision is the same quote, not a new
        # one. Legal because uniqueness is (number, version), not number alone.
        new.number = self.number
        Quotation.objects.filter(pk=new.pk).update(number=self.number)
        for item in self.items.all():
            item.clone_to(new)
        return new


class QuotationItem(TimeStampedModel):
    """One part on a quotation. Its own artwork, material and stations later."""

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='items')
    position = models.PositiveSmallIntegerField(default=1)

    item_code = models.ForeignKey(
        ItemCode, on_delete=models.PROTECT, null=True, blank=True, related_name='quote_items',
    )
    description = models.CharField(max_length=200, blank=True)
    # The artwork reference is quoted long before an SOItem (and therefore a
    # DrawingRevision) exists, so it lives here.
    artwork_ref = models.CharField(max_length=60, blank=True)

    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True)
    thickness = models.ForeignKey(Thickness, on_delete=models.PROTECT, null=True, blank=True)
    finish = models.ForeignKey(Finish, on_delete=models.PROTECT, null=True, blank=True)
    size = models.CharField(max_length=60, blank=True)

    quantity = models.PositiveIntegerField(default=0)
    tooling_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f'{self.quotation.number} #{self.position} {self.item_code or self.description}'

    @property
    def applicable_tier(self):
        """Slab pricing: the tier whose range contains the ordered quantity."""
        for tier in self.tiers.all():
            upper = tier.qty_to or 10**9
            if tier.qty_from <= self.quantity <= upper:
                return tier
        return self.tiers.last()

    @property
    def effective_rate(self):
        tier = self.applicable_tier
        return tier.rate_per_unit if tier else Decimal('0.00')

    @property
    def line_amount(self):
        return (self.effective_rate * self.quantity).quantize(Decimal('0.01'))

    @property
    def needs_code_approval(self):
        """True when this line would change what the master holds."""
        if not self.item_code:
            return False
        if self.item_code.approval_state == ItemCodeApproval.PENDING:
            return True
        return self.item_code.approved_rate != self.effective_rate

    def clone_to(self, quotation):
        clone = QuotationItem.objects.create(
            quotation=quotation, position=self.position, item_code=self.item_code,
            description=self.description, material=self.material,
            thickness=self.thickness, finish=self.finish, size=self.size,
            quantity=self.quantity, tooling_charge=self.tooling_charge,
        )
        for tier in self.tiers.all():
            PriceTier.objects.create(
                item=clone, qty_from=tier.qty_from, qty_to=tier.qty_to,
                rate_per_unit=tier.rate_per_unit,
            )
        return clone


class PriceTier(TimeStampedModel):
    """A quantity slab and its rate (§3.2 — typed manually, no auto-pricing)."""

    item = models.ForeignKey(QuotationItem, on_delete=models.CASCADE, related_name='tiers')
    qty_from = models.PositiveIntegerField(default=0)
    qty_to = models.PositiveIntegerField(null=True, blank=True, help_text='Blank = no upper limit')
    rate_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ['qty_from']

    def __str__(self):
        upper = self.qty_to if self.qty_to else '+'
        return f'{self.qty_from}-{upper} @ {self.rate_per_unit}'

    @property
    def estimated_amount(self):
        basis = self.qty_to or self.qty_from
        return (self.rate_per_unit * basis).quantize(Decimal('0.01'))
