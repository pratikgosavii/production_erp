"""Sheets, offcuts and outward dispatch (§3.9, §10.1·3, §10.1·8).

A sheet's QR label encodes a full scan URL rather than a bare id, so any phone
camera opens the sheet form — the old system encoded the raw pk and only worked
from inside the app.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse

from core.constants import (
    PREFIX_OFFCUT, PREFIX_SHEET, SheetStatus,
)
from core.models import Sequence, TimeStampedModel
from masters.models import Finish, Grade, Material, Rack, SheetSize, Supplier, Thickness


class Sheet(TimeStampedModel):
    """A physical sheet or offcut. Status is driven by workflow, never typed."""

    uid = models.CharField(max_length=20, unique=True, editable=False)
    is_offcut = models.BooleanField(default=False)
    # Lineage: an offcut keeps a link to the sheet it was cut from so cost and
    # yield roll up to the original purchase.
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='offcuts',
    )

    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name='sheets')
    thickness = models.ForeignKey(Thickness, on_delete=models.PROTECT, related_name='sheets')
    grade = models.ForeignKey(
        Grade, on_delete=models.PROTECT, null=True, blank=True, related_name='sheets',
    )
    size = models.ForeignKey(SheetSize, on_delete=models.PROTECT, null=True, blank=True)
    size_note = models.CharField(max_length=60, blank=True, help_text='Free size for offcuts')
    # Offcuts have no catalogue size, so carry their own dimensions.
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)
    finish = models.ForeignKey(
        Finish, on_delete=models.SET_NULL, null=True, blank=True, related_name='sheets',
    )
    finish_other = models.CharField(max_length=60, blank=True)
    remarks = models.CharField(max_length=250, blank=True)

    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='sheets',
    )
    rack = models.ForeignKey(
        Rack, on_delete=models.SET_NULL, null=True, blank=True, related_name='sheets',
    )
    received_on = models.DateField(null=True, blank=True)
    purchase_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=SheetStatus.choices, default=SheetStatus.AVAILABLE,
    )
    document = models.FileField(upload_to='sheets/', blank=True)

    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='logged_sheets',
    )

    # QR label provenance — which print batch the sticker came from, and when
    # the sheet was last scanned on the floor.
    label_batch = models.CharField(max_length=30, blank=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    last_scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sheet_scans',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status']), models.Index(fields=['material', 'thickness'])]

    def __str__(self):
        return self.uid

    def save(self, *args, **kwargs):
        if not self.uid:
            prefix = PREFIX_OFFCUT if self.is_offcut else PREFIX_SHEET
            self.uid = Sequence.next_number(prefix, start=100)
        super().save(*args, **kwargs)

    @property
    def scan_url(self):
        """What the QR encodes — a real URL, so a plain camera app works."""
        return reverse('stores:sheet_detail', args=[self.uid])

    @property
    def size_display(self):
        return self.size.name if self.size else self.size_note

    @property
    def origin(self):
        if self.is_offcut and self.parent:
            return f'From {self.parent.uid} (leftover)'
        return f'Purchased — {self.supplier.name}' if self.supplier else 'Purchased'

    @property
    def active_reservation(self):
        return self.reservations.filter(released_at__isnull=True).first()

    @property
    def can_be_issued(self):
        """Only a sheet Design has reserved may be issued (§10.1·3)."""
        return self.status == SheetStatus.RESERVED


class DispatchRecord(TimeStampedModel):
    """§10.1·8 — QA marks ready, the Store in-charge marks outward done."""

    order = models.ForeignKey(
        'accounts.SalesOrder', on_delete=models.CASCADE, related_name='dispatches',
    )
    ready_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='dispatches',
    )
    remarks = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        state = 'Dispatched' if self.dispatched_at else 'Ready'
        return f'{self.order.number} — {state}'

    @property
    def is_dispatched(self):
        return self.dispatched_at is not None
