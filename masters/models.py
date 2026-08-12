"""Reference data every other module reads (§6 masters app).

Maintained by Admin. Sales never edits these directly — an unknown item code
typed on a quotation is created here automatically and approved by the Sales
Manager along with the quote.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.constants import ApprovalSource, ItemCodeApproval
from core.models import TimeStampedModel

# A band with no upper limit runs to "everything above qty_from". Comparisons
# need a real number, and this is far past any quantity this shop will quote.
BAND_OPEN_END = 10 ** 9


class BandOverlapError(ValidationError):
    """Two bands would cover the same quantity, so the rate card is ambiguous.

    A ValidationError subclass on purpose: forms and the admin already handle
    it, and the callers that need to tell this apart from any other validation
    failure (the quotation save path, Quotation.approve) can catch it by name.
    """


def normalise_band_range(qty_from, qty_to):
    """(qty_from, qty_to) as the rate card stores it.

    A band starts at 1 — quantity 0 is not an order — and quotation price tiers
    conventionally start at 0, so this normalises rather than rejects. An upper
    limit below the lower one is meaningless and becomes "no upper limit".
    """
    qty_from = max(1, int(qty_from or 0))
    if qty_to is not None:
        qty_to = int(qty_to)
        if qty_to < qty_from:
            qty_to = None
    return qty_from, qty_to


def band_range_label(qty_from, qty_to):
    """'1–100' / '301+' — the same wording ItemCodeRate.range_label uses."""
    return f'{qty_from}–{qty_to}' if qty_to else f'{qty_from}+'


def band_overlap_message(code, qty_from, qty_to, clashes):
    """The sentence the salesperson and the Sales Manager both get to read."""
    qty_from, qty_to = normalise_band_range(qty_from, qty_to)
    slab = band_range_label(qty_from, qty_to)
    names = ', '.join(band.range_label for band in clashes)
    plural = 'bands' if len(clashes) > 1 else 'band'
    text = (f'{code.code}: slab {slab} overlaps the approved {plural} {names} '
            f'on {code.customer.name}’s rate card.')

    # Say how to fix it, not just that it is broken: the first quantity above
    # whatever it runs into. Only offered when that range is genuinely free —
    # advice that collides with the NEXT band would be worse than none.
    below = [b.qty_to for b in clashes if b.qty_from <= qty_from and b.qty_to is not None]
    if below:
        start = max(below) + 1
        upper = BAND_OPEN_END if qty_to is None else qty_to
        if start <= upper and not code.overlapping_bands(start, qty_to):
            return text + (f' Adjust the slab to {band_range_label(start, qty_to)} '
                           f'or revise the existing band.')
    return text + (' Quote one of those bands’ exact range to revise it, or move the '
                   'slab to a quantity the rate card does not already cover.')


class NamedMaster(TimeStampedModel):
    """Shared shape for the simple lookup lists."""

    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['name']

    def __str__(self):
        return self.name


class Material(NamedMaster):
    """SS 304, Brass, Aluminium..."""


class Thickness(NamedMaster):
    """Stored as text ('1.5mm') because it is a catalogue value, not a measure."""

    value_mm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta(NamedMaster.Meta):
        verbose_name_plural = 'thicknesses'


class Grade(NamedMaster):
    """Commercial / Prime / Secondary."""


class Finish(NamedMaster):
    """Black Epoxy Filled, Anodized, Laminated..."""

    class Meta(NamedMaster.Meta):
        verbose_name_plural = 'finishes'


class SheetSize(TimeStampedModel):
    """A stock sheet size, e.g. 8x4 ft."""

    name = models.CharField(max_length=60, unique=True)
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    address = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Rack(NamedMaster):
    """Physical storage location — Rack A-1, Shop floor..."""


class Customer(TimeStampedModel):
    """§3.2 Bill-To — printed at the top of the quotation PDF."""

    name = models.CharField(max_length=150, unique=True)
    address = models.CharField(max_length=250, blank=True)
    # City is stored rather than parsed out of the address: the customers
    # screen filters on it, and splitting free text is unreliable.
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    gstin = models.CharField('GSTIN / Tax ID', max_length=20, blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ItemCode(TimeStampedModel):
    """A customer's part number, carrying its RATE CARD and its spec.

    Scoped per customer: the same string may exist for two customers and they
    are different parts. Created automatically when a salesperson types an
    unknown code, then approved by the Sales Manager when the quotation is
    approved.

    The rate card is a set of quantity BANDS (``rate_bands``), e.g.
    ``1-100 @ 175 | 101-200 @ 111 | 201-500 @ 95``. Each band is approved
    independently, once, and is then reused forever without re-approval.
    ``approved_rate`` below is only a convenience MIRROR of the lowest-quantity
    approved band, kept so the older screens keep rendering — the bands are the
    source of truth.
    """

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='item_codes')
    code = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)

    material = models.ForeignKey(Material, on_delete=models.SET_NULL, null=True, blank=True)
    thickness = models.ForeignKey(Thickness, on_delete=models.SET_NULL, null=True, blank=True)
    size = models.CharField(max_length=60, blank=True, help_text='Part size, e.g. 150 x 110')
    finish = models.ForeignKey(Finish, on_delete=models.SET_NULL, null=True, blank=True)

    approval_state = models.CharField(
        max_length=12, choices=ItemCodeApproval.choices, default=ItemCodeApproval.PENDING,
    )
    approved_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Mirror of the lowest-quantity approved rate band. '
                  'The bands on the rate card are the source of truth.',
    )
    approved_on = models.DateField(null=True, blank=True)
    approved_via = models.CharField(
        max_length=20, blank=True, help_text='Quotation number the rate was approved on',
    )
    approval_source = models.CharField(
        max_length=12, choices=ApprovalSource.choices, default=ApprovalSource.QUOTATION,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_item_codes',
    )
    # A proposed rate sits here while it waits for approval, so the live
    # approved_rate keeps working on other quotations in the meantime.
    proposed_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    revision_reason = models.CharField(max_length=250, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['customer__name', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['customer', 'code'], name='uniq_item_code_per_customer',
            ),
        ]

    def __str__(self):
        return f'{self.code} ({self.customer.name})'

    @property
    def is_approved(self):
        return self.approval_state == ItemCodeApproval.APPROVED

    @property
    def spec_line(self):
        bits = [
            self.material.name if self.material else '',
            self.thickness.name if self.thickness else '',
            self.size,
            self.finish.name if self.finish else '',
        ]
        return ' · '.join(b for b in bits if b)

    # ---- rate card (bands are authoritative) ----------------------------
    def band_for(self, quantity):
        """The band whose range CONTAINS `quantity`. None when none does.

        There is deliberately no fallback: a quantity outside every band is a
        gap in the rate card, and the Sales Manager has to price it.
        """
        if not quantity:
            return None
        for band in self.rate_bands.all():
            upper = BAND_OPEN_END if band.qty_to is None else band.qty_to
            if band.qty_from <= quantity <= upper:
                return band
        return None

    def approved_band_for(self, quantity):
        """`band_for`, but only if that band has actually been approved.

        A band carrying a pending REVISION still counts: its approved_rate is
        the figure the Sales Manager signed off and it keeps pricing quotes
        until the revision is approved. A band that has never been approved
        (proposed_rate only, no approved_rate) does not count.
        """
        band = self.band_for(quantity)
        if band is None or band.approved_rate is None:
            return None
        return band

    @property
    def approved_bands(self):
        """The live rate card — approved bands only, lowest quantity first."""
        return self.rate_bands.filter(
            approval_state=ItemCodeApproval.APPROVED).order_by('qty_from')

    @property
    def has_bands(self):
        return self.rate_bands.exists()

    def overlapping_bands(self, qty_from, qty_to, exclude_pk=None):
        """Existing bands that PARTIALLY overlap `qty_from..qty_to`.

        An exact-range hit is not a collision — that is the same band being
        revised. Anything else that shares even one quantity is, because two
        bands covering the same quantity make the rate card ambiguous and
        `band_for` would silently pick whichever starts lowest.
        """
        qty_from, qty_to = normalise_band_range(qty_from, qty_to)
        upper = BAND_OPEN_END if qty_to is None else qty_to
        clashes = []
        for other in self.rate_bands.all():
            if exclude_pk is not None and other.pk == exclude_pk:
                continue
            if other.qty_from == qty_from and other.qty_to == qty_to:
                continue
            if qty_from <= other.upper_bound and other.qty_from <= upper:
                clashes.append(other)
        return sorted(clashes, key=lambda b: b.qty_from)

    def add_or_revise_band(self, qty_from, qty_to, rate, reason=''):
        """Put `rate` on the band `qty_from..qty_to`, returning (band, kind).

        kind is one of:
          'unchanged' — that exact band is already approved at that rate, so
                        nothing at all happens (an approved band is permanent).
          'revised'   — the band exists at a different rate: the new figure is
                        parked in proposed_rate and the band goes back to
                        PENDING, while approved_rate keeps pricing live quotes.
          'new'       — no band with that range: one is created PENDING.

        A range that PARTIALLY overlaps an existing band is none of those: it
        would straddle the rate card, so it raises BandOverlapError rather than
        quietly becoming a fourth, ambiguous band.
        """
        rate = Decimal(rate)
        qty_from, qty_to = normalise_band_range(qty_from, qty_to)

        band = self.rate_bands.filter(qty_from=qty_from, qty_to=qty_to).first()
        if band is None:
            clashes = self.overlapping_bands(qty_from, qty_to)
            if clashes:
                raise BandOverlapError(
                    band_overlap_message(self, qty_from, qty_to, clashes))
            band = ItemCodeRate.objects.create(
                item_code=self, qty_from=qty_from, qty_to=qty_to,
                rate_per_unit=rate, proposed_rate=rate,
                approval_state=ItemCodeApproval.PENDING,
                revision_reason=reason,
            )
            return band, 'new'

        if band.approved_rate is not None and band.approved_rate == rate \
                and band.approval_state == ItemCodeApproval.APPROVED:
            return band, 'unchanged'

        band.proposed_rate = rate
        band.rate_per_unit = rate
        band.approval_state = ItemCodeApproval.PENDING
        band.revision_reason = reason
        band.save(update_fields=[
            'proposed_rate', 'rate_per_unit', 'approval_state',
            'revision_reason', 'updated_at',
        ])
        return band, 'revised'

    def refresh_approved_rate(self):
        """Re-point the legacy `approved_rate` mirror at the lowest band.

        Called after any band change. It never CLEARS the mirror: a code that
        has no approved band yet simply keeps whatever it had (historically
        that is None), so nothing that reads the old field breaks.
        """
        band = self.approved_bands.first()
        if band is None or band.approved_rate is None:
            return None
        self.approved_rate = band.approved_rate
        self.proposed_rate = None
        self.approval_state = ItemCodeApproval.APPROVED
        self.approved_on = band.approved_on
        self.approved_via = band.approved_via
        self.approved_by = band.approved_by
        self.save(update_fields=[
            'approved_rate', 'proposed_rate', 'approval_state',
            'approved_on', 'approved_via', 'approved_by', 'updated_at',
        ])
        return band

    def approve(self, rate, user, quotation_no=''):
        """Lock a rate in on the legacy single-rate mirror.

        The RATE BANDS are authoritative; this only stamps the convenience
        fields on the code itself. Quotation.approve() approves the band that
        covers the quoted quantity and then calls refresh_approved_rate().
        """
        from datetime import date
        self.approved_rate = rate
        self.proposed_rate = None
        self.approval_state = ItemCodeApproval.APPROVED
        self.approved_on = date.today()
        self.approved_by = user
        self.approved_via = quotation_no
        self.save(update_fields=[
            'approved_rate', 'proposed_rate', 'approval_state',
            'approved_on', 'approved_by', 'approved_via', 'updated_at',
        ])

    def propose_revision(self, rate, reason=''):
        """A changed rate is not trusted until re-approved (§ rate revision).

        Mirror-level only, same as approve(): the band carrying the quoted
        quantity is what actually goes back to PENDING — see
        ItemCode.add_or_revise_band().
        """
        self.proposed_rate = rate
        self.revision_reason = reason
        self.approval_state = ItemCodeApproval.PENDING
        self.save(update_fields=[
            'proposed_rate', 'revision_reason', 'approval_state', 'updated_at',
        ])


class ItemCodeRate(TimeStampedModel):
    """One quantity band on a customer's rate card for an item code.

    ``VF-DP-080: 1-100 @ 175 | 101-200 @ 111 | 201-500 @ 95`` is three rows.
    Each is approved on its own, once, by the Sales Manager — and once approved
    it is reused forever, on every later quotation, without re-approval.

    ``approved_rate`` is the live figure. ``proposed_rate`` holds a change that
    is still waiting for sign-off, so the approved rate keeps pricing quotes in
    the meantime.
    """

    item_code = models.ForeignKey(
        ItemCode, on_delete=models.CASCADE, related_name='rate_bands',
    )
    qty_from = models.PositiveIntegerField()
    qty_to = models.PositiveIntegerField(
        null=True, blank=True, help_text='Blank = no upper limit',
    )
    rate_per_unit = models.DecimalField(max_digits=10, decimal_places=2)

    approval_state = models.CharField(
        max_length=12, choices=ItemCodeApproval.choices,
        default=ItemCodeApproval.PENDING,
    )
    approved_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='The live, approved figure for this band.',
    )
    proposed_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='A pending change; approved_rate keeps working until it is signed off.',
    )
    approved_via = models.CharField(
        max_length=20, blank=True, help_text='Quotation number the band was approved on',
    )
    approved_on = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_rate_bands',
    )
    approval_source = models.CharField(
        max_length=12, choices=ApprovalSource.choices, default=ApprovalSource.QUOTATION,
    )
    revision_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['qty_from']
        constraints = [
            models.UniqueConstraint(
                fields=['item_code', 'qty_from', 'qty_to'], name='uniq_rate_band_range',
            ),
        ]

    def __str__(self):
        upper = self.qty_to if self.qty_to else '+'
        rate = self.approved_rate if self.approved_rate is not None else self.rate_per_unit
        return f'{self.item_code.code} {self.qty_from}-{upper} @ {rate}'

    @property
    def is_approved(self):
        return self.approval_state == ItemCodeApproval.APPROVED

    @property
    def upper_bound(self):
        return BAND_OPEN_END if self.qty_to is None else self.qty_to

    @property
    def range_label(self):
        return f'{self.qty_from}–{self.qty_to}' if self.qty_to else f'{self.qty_from}+'

    @property
    def live_rate(self):
        """What this band prices at today — the approved figure, if any."""
        if self.approved_rate is not None:
            return self.approved_rate
        return self.rate_per_unit

    def covers(self, quantity):
        return bool(quantity) and self.qty_from <= quantity <= self.upper_bound

    def clean(self):
        """A rate card may not be ambiguous: bands must not overlap."""
        if self.qty_from is None or self.qty_from < 1:
            raise ValidationError({'qty_from': 'A band must start at 1 or more.'})
        if self.qty_to is not None and self.qty_to < self.qty_from:
            raise ValidationError(
                {'qty_to': 'The upper limit cannot be below the lower limit.'})
        if not self.item_code_id:
            return

        # Same range as an existing row is a REVISION of that row, not an
        # overlap — the unique constraint is what forbids duplicating it.
        others = ItemCodeRate.objects.filter(item_code_id=self.item_code_id)
        if self.pk:
            others = others.exclude(pk=self.pk)
        upper = BAND_OPEN_END if self.qty_to is None else self.qty_to
        for other in others:
            if self.qty_from <= other.upper_bound and other.qty_from <= upper:
                raise BandOverlapError(
                    f'This band ({self.range_label}) overlaps the existing band '
                    f'{other.range_label} on {self.item_code.code}.')

    def save(self, *args, **kwargs):
        """Validate on EVERY write — clean() alone is not a rule, it is advice.

        Django never calls full_clean() on save(), so before this
        ``ItemCodeRate.objects.create(...)``, the seeder and Quotation.approve()
        all walked straight past the overlap check while the admin and
        ModelForms honoured it. Validating here is what makes the rule
        unbypassable: a straddling band cannot reach the database from ANY
        path. Forms simply validate twice, which is harmless.

        ``update_fields`` writes are validated too — the instance is loaded
        from the row it is about to overwrite, so every field is real.
        """
        self.full_clean()
        super().save(*args, **kwargs)

    def approve(self, user, quotation_no='', source=ApprovalSource.QUOTATION):
        """Sign this band off. The proposed figure becomes the live one."""
        from datetime import date
        if self.proposed_rate is not None:
            self.approved_rate = self.proposed_rate
        elif self.approved_rate is None:
            self.approved_rate = self.rate_per_unit
        self.rate_per_unit = self.approved_rate
        self.proposed_rate = None
        self.approval_state = ItemCodeApproval.APPROVED
        self.approved_via = quotation_no
        self.approved_on = date.today()
        self.approved_by = user
        self.approval_source = source
        self.save(update_fields=[
            'approved_rate', 'rate_per_unit', 'proposed_rate', 'approval_state',
            'approved_via', 'approved_on', 'approved_by', 'approval_source',
            'updated_at',
        ])
        return self


class ProcessStation(TimeStampedModel):
    """The station master (§3.8). The Floor Manager picks which apply per job."""

    name = models.CharField(max_length=120, unique=True)
    sequence = models.PositiveSmallIntegerField(default=0)
    # Cell/group the station belongs to, used for the operator roster.
    group = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sequence', 'name']

    def __str__(self):
        return self.name


class ReorderLevel(TimeStampedModel):
    """§10.2·6 — set by Admin, per material+thickness, not by Stores."""

    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    thickness = models.ForeignKey(Thickness, on_delete=models.CASCADE)
    minimum_qty = models.PositiveIntegerField(default=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['material', 'thickness'], name='uniq_reorder_material_thickness',
            ),
        ]

    def __str__(self):
        return f'{self.material} {self.thickness} — min {self.minimum_qty}'
