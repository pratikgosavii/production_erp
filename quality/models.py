"""Non-Conformance and Corrective Action (NCR / CAPA).

One record type serves five of the audit non-conformances the spec lists,
because they are the same shape: something was wrong, somebody has to find out
why, and somebody has to stop it happening again. Only the SOURCE differs.

A production rejection already captures qty, reason and disposition at the
station. Rather than ask the floor to fill a second form, an NCR is raised FROM
that log automatically once a rejection is big enough to be worth investigating
- the data is the same, it is only being asked a harder question.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.constants import PREFIX_NCR, NCRSource, NCRStatus, Severity
from core.models import Sequence, TimeStampedModel


class NCR(TimeStampedModel):
    """One non-conformance, from whatever corner of the business it came."""

    number = models.CharField(max_length=20, unique=True, editable=False)
    source = models.CharField(max_length=20, choices=NCRSource.choices)
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.MINOR,
    )
    status = models.CharField(
        max_length=16, choices=NCRStatus.choices, default=NCRStatus.OPEN,
    )

    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)

    # Where it came from. All optional: a supplier NC has no job card, and a
    # production rejection has no complaint reference.
    job_card_station = models.ForeignKey(
        'production.JobCardStation', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ncrs',
    )
    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ncrs',
    )
    qty_affected = models.PositiveIntegerField(default=0)

    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ncrs_raised',
    )
    raised_at = models.DateTimeField(default=timezone.now)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ncrs_owned',
        help_text='Who has to answer it.',
    )
    due_date = models.DateField(null=True, blank=True)

    # The CAPA half. Empty until somebody does the thinking.
    root_cause = models.TextField(blank=True)
    corrective_action = models.TextField(blank=True)
    preventive_action = models.TextField(blank=True)

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ncrs_closed',
    )
    verification_note = models.TextField(blank=True)

    class Meta:
        ordering = ['-raised_at', '-id']

    def __str__(self):
        return f'{self.number} - {self.title}'

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number(PREFIX_NCR, start=1)
        super().save(*args, **kwargs)

    # ---- state ---------------------------------------------------------
    @property
    def is_open(self):
        return self.status != NCRStatus.CLOSED

    @property
    def is_overdue(self):
        return bool(self.is_open and self.due_date
                    and self.due_date < timezone.localdate())

    @property
    def age_days(self):
        return (timezone.now() - self.raised_at).days

    @property
    def has_capa(self):
        """A root cause AND an action - either alone is half an answer."""
        return bool(self.root_cause.strip() and self.corrective_action.strip())

    @property
    def origin_label(self):
        if self.job_card_station_id:
            row = self.job_card_station
            return f'{row.job_card.number} - {row.station.name}'
        if self.so_item_id:
            return str(self.so_item.order.number)
        return '-'

    # ---- transitions ---------------------------------------------------
    @transaction.atomic
    def close(self, user, note=''):
        """Closing requires the thinking to have been done, not just a click."""
        if not self.has_capa:
            raise ValidationError(
                'An NCR cannot be closed without a root cause and a corrective '
                'action - closing it empty is how the same defect comes back.')
        self.status = NCRStatus.CLOSED
        self.closed_at = timezone.now()
        self.closed_by = user
        self.verification_note = (note or '').strip()
        self.save(update_fields=['status', 'closed_at', 'closed_by',
                                 'verification_note', 'updated_at'])
        return self

    # ---- creation from a production rejection --------------------------
    #: Below this a rejection is normal process loss, and raising paperwork for
    #: it would only train everyone to ignore NCRs.
    AUTO_RAISE_QTY = 5
    AUTO_RAISE_PERCENT = 10

    @classmethod
    def should_auto_raise(cls, station_row):
        """Is this rejection big enough to be worth investigating?"""
        rejected = station_row.qty_rejected or 0
        if not rejected:
            return False
        if rejected >= cls.AUTO_RAISE_QTY:
            return True
        went_in = station_row.qty_in or 0
        return bool(went_in and (rejected / went_in * 100) >= cls.AUTO_RAISE_PERCENT)

    @classmethod
    @transaction.atomic
    def raise_from_station(cls, station_row, user=None):
        """Open an NCR against a station rejection, once."""
        existing = cls.objects.filter(job_card_station=station_row).first()
        if existing is not None:
            return existing

        rejected = station_row.qty_rejected or 0
        went_in = station_row.qty_in or 0
        pct = (rejected / went_in * 100) if went_in else 0
        severity = Severity.MAJOR if (rejected >= 20 or pct >= 25) else Severity.MINOR

        link = station_row.job_card.items.select_related('so_item').first()
        detail = (
            f'{rejected} of {went_in} rejected at {station_row.station.name} '
            f'({pct:.0f}%). '
            f'Reason: {station_row.get_rejection_reason_display() or "-"}. '
            f'Disposition: {station_row.get_disposition_display() or "-"}. '
            f'Operator: {station_row.operator.short_name if station_row.operator else "-"}.'
        )
        return cls.objects.create(
            source=NCRSource.PRODUCTION,
            severity=severity,
            title=(f'{station_row.get_rejection_reason_display() or "Rejection"} at '
                   f'{station_row.station.name} - {station_row.job_card.number}'),
            detail=detail,
            job_card_station=station_row,
            so_item=link.so_item if link else None,
            qty_affected=rejected,
            raised_by=user,
            due_date=timezone.localdate() + timedelta(days=7),
        )
