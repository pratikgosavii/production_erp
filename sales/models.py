"""Quotation, its line items and their batch-wise price tiers (§3.2, §3.3).

A quotation lives under its QTN only. No Sales Order exists until Accounts
confirm the PO (§1.1) — that is why the PO fields hang off this model rather
than off SalesOrder.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction

from core.constants import (
    PREFIX_QUOTATION, Currency, OrderType, QuotationStatus,
)
from core.models import Sequence, TimeStampedModel
from masters.models import (
    BandOverlapError, Customer, Finish, ItemCode, Material, Thickness,
    band_overlap_message, band_range_label, normalise_band_range,
)


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

    # Handed to Accounts for booking. The status stays PO_UPLOADED — that is
    # what the Order Booking queue reads — but this stamp is what stops the
    # salesperson re-sending it and keeps "Convert to Sales Order" from
    # sitting there after it has already gone.
    sent_to_accounts_at = models.DateTimeField(null=True, blank=True)

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
    def is_with_accounts(self):
        """Handed over and waiting on Accounts to confirm the PO."""
        return (self.status == QuotationStatus.PO_UPLOADED
                and self.sent_to_accounts_at is not None)

    @property
    def unapproved_items(self):
        """Line items whose item code still needs the manager's sign-off."""
        return [i for i in self.items.select_related('item_code') if i.needs_code_approval]

    @property
    def band_conflicts(self):
        """Every typed slab on this quote that would straddle the rate card.

        [(item, tier, conflicting_band)] — aggregated across the line items so
        the salesperson is stopped at SUBMIT, long before a Sales Manager can
        click Approve and meet an exception.
        """
        found = []
        for item in self.items.select_related('item_code__customer').prefetch_related(
                'tiers', 'item_code__rate_bands'):
            for tier, band in item.band_conflicts:
                found.append((item, tier, band))
        return found

    @property
    def band_conflict_text(self):
        """The conflicts as one readable sentence per clashing slab."""
        lines, seen = [], set()
        for item, tier, _band in self.band_conflicts:
            key = (item.pk, tier.pk)
            if key in seen:
                continue
            seen.add(key)
            clashes = item.item_code.overlapping_bands(tier.qty_from, tier.qty_to)
            lines.append(band_overlap_message(
                item.item_code, tier.qty_from, tier.qty_to, clashes))
        return ' '.join(lines)

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
        """Approving the quote signs off EVERY slab the salesperson quoted.

        The whole ladder is locked in, not just the band the quoted quantity
        happens to land in. A salesperson who types 1-100 / 101-200 / 201-300
        has priced all three, the manager has seen all three, and the customer
        was given all three — so a later PO for 250 must not come back for
        another approval.

        Bands this quotation does NOT mention are left exactly as they are; an
        approved band is permanent.

        A quote carrying a straddling slab is refused outright, before the
        first band is written: the whole approval is atomic, so a rate card is
        never left half-updated with the quotation still pending.
        """
        from django.utils import timezone

        conflict = self.band_conflict_text
        if conflict:
            raise BandOverlapError(
                f'{self.number} cannot be approved — {conflict} '
                f'Nothing has been written to the rate card; send the quotation '
                f'back so the slab can be corrected.')

        for item in self.items.select_related('item_code').prefetch_related('tiers'):
            code = item.item_code
            if code is None:
                continue
            for tier in item.tiers.all():
                if not tier.rate_per_unit:
                    # A blank slab row the salesperson never filled in.
                    continue
                band, kind = code.add_or_revise_band(
                    tier.qty_from, tier.qty_to, tier.rate_per_unit,
                    reason=f'Quoted on {self.number}',
                )
                # 'unchanged' — that exact band is already approved at that rate.
                # 'covered'   — a narrower slab inside an approved band at the
                #               same price; the rate card is untouched, so there
                #               is nothing to sign off.
                if kind not in ('unchanged', 'covered') and band is not None:
                    band.approve(user, self.number)
            code.refresh_approved_rate()

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
        """Slab pricing: the tier whose range CONTAINS the ordered quantity.

        No fallback. A quantity outside every slab is a hole in the ladder the
        salesperson has to fix — see `has_slab_gap`.
        """
        for tier in self.tiers.all():
            upper = tier.qty_to if tier.qty_to is not None else 10**9
            if tier.qty_from <= self.quantity <= upper:
                return tier
        return None

    @property
    def has_slab_gap(self):
        """A real quantity that no price tier covers."""
        return bool(self.quantity) and self.applicable_tier is None

    @property
    def effective_rate(self):
        tier = self.applicable_tier
        return tier.rate_per_unit if tier else Decimal('0.00')

    @property
    def line_amount(self):
        return (self.effective_rate * self.quantity).quantize(Decimal('0.01'))

    @property
    def unapproved_tiers(self):
        """Every quoted slab that is not already on the rate card at that rate.

        approve() writes the whole ladder, so the manager is signing off every
        slab — not merely the one the quoted quantity happens to land in.
        """
        code = self.item_code
        if code is None:
            return []
        pending = []
        for tier in self.tiers.all():
            if not tier.rate_per_unit:
                continue
            # Auto-approved: the slab falls inside approved bands that already
            # carry this exact price, so the rate card does not change and
            # there is no decision for the Sales Manager to make.
            if code.covered_at_rate(tier.qty_from, tier.qty_to, tier.rate_per_unit):
                continue
            band = code.band_for(tier.qty_from)
            same_range = (band is not None
                          and band.qty_from == tier.qty_from
                          and band.qty_to == tier.qty_to)
            if not same_range or not band.is_approved \
                    or band.approved_rate != tier.rate_per_unit:
                pending.append(tier)
        return pending

    @property
    def needs_code_approval(self):
        """True when any quoted slab would add to, or change, the rate card."""
        if not self.item_code:
            return False
        return bool(self.unapproved_tiers)

    @property
    def band_conflicts(self):
        """[(tier, conflicting_band)] — quoted slabs that straddle the rate card.

        EVERY tier is checked, because approve() now writes every tier. Checking
        only the applicable one would let a straddling slab through the submit
        gate and then blow up mid-approval.

        An exact range match is fine (that is a revision of that band) and so
        is a range that touches nothing (that is a new band).
        """
        code = self.item_code
        if code is None:
            return []
        found = []
        for tier in self.tiers.all():
            if not tier.rate_per_unit:
                continue
            # A slab sitting wholly inside approved bands at the same price is
            # not straddling anything — approve() writes nothing for it.
            if code.covered_at_rate(tier.qty_from, tier.qty_to, tier.rate_per_unit):
                continue
            # Inside one approved band at a different price: approvable by
            # carving that band, so it goes to the manager rather than being
            # refused at submit. Only a slab crossing a band boundary is a
            # genuine straddle.
            if code.containing_approved_band(tier.qty_from, tier.qty_to):
                continue
            for band in code.overlapping_bands(tier.qty_from, tier.qty_to):
                found.append((tier, band))
        return found

    @property
    def has_band_conflict(self):
        return bool(self.band_conflicts)

    @property
    def band_state(self):
        """'ok' | 'new_band' | 'revised_band' | 'no_slab' — for the UI badge."""
        if not self.item_code or not self.quantity:
            return 'ok'
        if self.applicable_tier is None:
            return 'no_slab'
        band = self.item_code.approved_band_for(self.quantity)
        if band is None:
            return 'new_band'
        if band.approved_rate != self.effective_rate:
            return 'revised_band'
        return 'ok'

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
    def range_label(self):
        """'1–100' / '301+' — a slab named the way the rate card names bands.

        Normalised first: a tier conventionally starts at 0 and the band it
        would become starts at 1, so this is the range that actually gets
        compared against the customer's rate card.
        """
        return band_range_label(*normalise_band_range(self.qty_from, self.qty_to))

    @property
    def estimated_amount(self):
        basis = self.qty_to or self.qty_from
        return (self.rate_per_unit * basis).quantize(Decimal('0.01'))
