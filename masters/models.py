"""Reference data every other module reads (§6 masters app).

Maintained by Admin. Sales never edits these directly — an unknown item code
typed on a quotation is created here automatically and approved by the Sales
Manager along with the quote.
"""
from django.conf import settings
from django.db import models

from core.constants import ApprovalSource, ItemCodeApproval
from core.models import TimeStampedModel


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
    """§3.2 Bill-To. Email matters: 'Send to Customer' mails the quote PDF here."""

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
    """A customer's part number, carrying its approved rate and spec.

    Scoped per customer: the same string may exist for two customers and they
    are different parts. Created automatically when a salesperson types an
    unknown code, then approved — with its rate — by the Sales Manager when the
    quotation is approved. Once approved the rate auto-fills on later quotes,
    and editing it sends the code back to PENDING.
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
        help_text='Locked in when the Sales Manager approves the quotation.',
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

    def approve(self, rate, user, quotation_no=''):
        """Lock a rate in. Called when the Sales Manager approves a quotation."""
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
        """A changed rate is not trusted until re-approved (§ rate revision)."""
        self.proposed_rate = rate
        self.revision_reason = reason
        self.approval_state = ItemCodeApproval.PENDING
        self.save(update_fields=[
            'proposed_rate', 'revision_reason', 'approval_state', 'updated_at',
        ])


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
