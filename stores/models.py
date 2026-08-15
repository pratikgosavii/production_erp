"""Sheets, offcuts and outward dispatch (§3.9, §10.1·3, §10.1·8).

A sheet's QR label encodes a full scan URL rather than a bare id, so any phone
camera opens the sheet form — the old system encoded the raw pk and only worked
from inside the app.
"""
import re
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from core.constants import (
    PREFIX_OFFCUT, PREFIX_SHEET, SheetStatus,
)
from core.models import Sequence, TimeStampedModel
from core.services import plate_area_sq_in
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

    @property
    def offcut_count(self):
        return self.offcuts.count()

    @property
    def total_used_sq_in(self):
        """Area booked off this sheet across every cut, for yield reporting."""
        return sum(
            (cut.used_sq_in for cut in self.cuts.all() if cut.used_sq_in is not None),
            Decimal('0.00'),
        )

    @property
    def can_be_cut(self):
        """A cut can only be booked on material that actually reached the floor.

        Still AVAILABLE means it is on the rack; RESERVED means Design nested it
        but Stores has not handed it over — neither has been near a saw. Already
        CONSUMED or LEFTOVER sheets stay cuttable so a second remnant off the
        same plate can still be booked.
        """
        return self.status in (
            SheetStatus.ISSUED, SheetStatus.CONSUMED, SheetStatus.LEFTOVER,
        )


def _mm_pair(size_text):
    """'150 x 110' -> (150, 110) so an offcut carries real dimensions too.

    Area is core.services.plate_area_sq_in's job; this only lifts the two
    numbers out for Sheet.length_mm / width_mm, and returns (None, None) when
    the free text is not a pair of dimensions.
    """
    parts = re.findall(r'\d+(?:\.\d+)?', str(size_text or ''))
    if len(parts) < 2:
        return None, None
    return int(float(parts[0])), int(float(parts[1]))


class SheetCut(TimeStampedModel):
    """One booking of "this much came off that sheet" (§10.1·3).

    This is the record that closes the loop between a sheet issued to the floor
    and a remnant landing back in stock: before it existed offcuts could be
    filtered and displayed but nothing in the app ever created one.
    """

    sheet = models.ForeignKey(Sheet, on_delete=models.CASCADE, related_name='cuts')
    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sheet_cuts',
    )
    # The offcut this cut produced, if any. A fully-consumed cut leaves none.
    offcut = models.ForeignKey(
        Sheet, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='produced_by_cut',
    )

    used_note = models.CharField(max_length=60, blank=True, help_text='e.g. 150 x 110')
    remnant_note = models.CharField(max_length=60, blank=True)
    # Parsed from the free-text sizes above by core.services.plate_area_sq_in,
    # so an unparseable note simply leaves the figure blank.
    used_sq_in = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    remnant_sq_in = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )

    cut_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sheet_cuts',
    )
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        tail = f' -> {self.offcut.uid}' if self.offcut else ' (fully consumed)'
        return f'{self.sheet.uid} cut {self.used_note}{tail}'

    @classmethod
    @transaction.atomic
    def record(cls, sheet, user, used_note, remnant_note='', so_item=None,
               rack=None, remarks=''):
        """Book one cut, minting the remnant sheet when there is one.

        Returns ``(cut, offcut_or_None)``. The parent lands on LEFTOVER when a
        remnant came off it and CONSUMED when it did not — the status is derived
        from what was booked, never typed.
        """
        used_note = (used_note or '').strip()
        remnant_note = (remnant_note or '').strip()

        cut = cls.objects.create(
            sheet=sheet, so_item=so_item, used_note=used_note,
            remnant_note=remnant_note,
            used_sq_in=plate_area_sq_in(used_note),
            remnant_sq_in=plate_area_sq_in(remnant_note),
            cut_by=user, remarks=remarks,
        )

        offcut = None
        if remnant_note:
            length_mm, width_mm = _mm_pair(remnant_note)
            offcut = Sheet.objects.create(
                is_offcut=True, parent=sheet,
                material=sheet.material, thickness=sheet.thickness,
                grade=sheet.grade, finish=sheet.finish,
                finish_other=sheet.finish_other,
                size_note=remnant_note,
                length_mm=length_mm, width_mm=width_mm,
                rack=rack or sheet.rack,
                status=SheetStatus.AVAILABLE,
                logged_by=user,
                received_on=timezone.localdate(),
                remarks=f'Leftover from {sheet.uid}',
            )
            cut.offcut = offcut
            cut.save(update_fields=['offcut', 'updated_at'])

        sheet.status = SheetStatus.LEFTOVER if offcut else SheetStatus.CONSUMED
        sheet.save(update_fields=['status', 'updated_at'])
        return cut, offcut


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
