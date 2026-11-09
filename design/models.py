"""Drawings, design queries and material reservation (§3.6, §7.1, §10.1·3)."""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from core.constants import (
    PROCESS_CHECKS, Process, ProofOutcome, QueryStatus, SheetStatus,
)
from core.models import TimeStampedModel


class DrawingRevision(TimeStampedModel):
    """§10.2·3 — Sales and Design can both upload; old revisions stay viewable."""

    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, related_name='drawing_revisions',
    )
    revision = models.PositiveSmallIntegerField(default=0)
    file = models.FileField(upload_to='drawings/')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='drawing_uploads',
    )
    source = models.CharField(max_length=30, blank=True, help_text='Sales / Design / Customer')
    is_current = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    #: What Sales/the customer supplies is an INPUT to design, not a revision
    #: of it. It never gets superseded and never satisfies the design gate —
    #: only a drawing the designer produced does.
    REFERENCE_SOURCES = ('Sales', 'Customer')

    @property
    def is_customer_reference(self):
        return self.source in self.REFERENCE_SOURCES

    class Meta:
        ordering = ['-revision']
        constraints = [
            models.UniqueConstraint(
                fields=['so_item', 'revision'], name='uniq_revision_per_item',
            ),
        ]

    def __str__(self):
        return f'{self.so_item} Rev {self.revision:02d}'

    @property
    def label(self):
        return f'Rev {self.revision:02d}'

    @transaction.atomic
    def save(self, *args, **kwargs):
        # A customer reference is never "the current drawing" — it is the
        # brief, not the answer. Marking one current made the design gate
        # pass on a file the designer never produced.
        if self.is_customer_reference:
            self.is_current = False
        if self.is_current:
            # Only one current revision per item; the rest stay readable.
            DrawingRevision.objects.filter(
                so_item=self.so_item, is_current=True,
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class ProofRound(TimeStampedModel):
    """One artwork proof round with the customer (spec SS 3, SS 6).

    The proof itself travels by hand: the designer downloads the drawing, the
    salesperson gets the customer's sign-off, and the outcome is recorded here.
    The ERP does not pretend to send it - it records what happened, which is
    the part that was previously only in somebody's head.

    Proof revisions are customer-facing and number independently of the
    designer's internal Rev 00/01 drawing ladder.
    """

    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, related_name='proof_rounds',
    )
    revision = models.PositiveSmallIntegerField(default=1)
    drawing = models.ForeignKey(
        DrawingRevision, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='proof_rounds',
        help_text='The drawing revision that was actually sent out.',
    )

    sent_at = models.DateTimeField(default=timezone.now)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proofs_sent',
    )
    outcome = models.CharField(
        max_length=12, choices=ProofOutcome.choices, default=ProofOutcome.SENT,
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proofs_recorded',
    )
    customer_comment = models.TextField(blank=True)

    class Meta:
        ordering = ['-revision']
        constraints = [
            models.UniqueConstraint(
                fields=['so_item', 'revision'], name='uniq_proof_rev_per_item',
            ),
        ]

    def __str__(self):
        return f'{self.so_item} Proof Rev {self.revision:02d}'

    @property
    def label(self):
        return f'Proof Rev {self.revision:02d}'

    @property
    def is_open(self):
        return self.outcome == ProofOutcome.SENT

    @property
    def days_waiting(self):
        if not self.is_open:
            return None
        return (timezone.now() - self.sent_at).days

    @transaction.atomic
    def record(self, outcome, user, comment=''):
        """Salesperson writes down what the customer said."""
        self.outcome = outcome
        self.customer_comment = (comment or '').strip()
        self.responded_at = timezone.now()
        self.recorded_by = user
        self.save(update_fields=[
            'outcome', 'customer_comment', 'responded_at', 'recorded_by',
            'updated_at'])
        return self


class DesignQuery(TimeStampedModel):
    """Routed to the exact salesperson who raised the originating quote (§7.1)."""

    order = models.ForeignKey(
        'accounts.SalesOrder', on_delete=models.CASCADE, related_name='queries',
    )
    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, null=True, blank=True,
        related_name='queries',
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='raised_queries',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_queries',
    )
    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=10, choices=QueryStatus.choices, default=QueryStatus.OPEN,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'design queries'

    def __str__(self):
        return f'{self.order.number}: {self.subject[:40]}'

    @classmethod
    def raise_for(cls, order, raised_by, subject, body, so_item=None):
        """Assignment is automatic — the quote's salesperson, not 'sales'."""
        query = cls.objects.create(
            order=order, so_item=so_item, raised_by=raised_by,
            assigned_to=order.salesperson, subject=subject,
        )
        QueryMessage.objects.create(query=query, author=raised_by, body=body)
        return query

    def reply(self, author, body, attachment=None):
        msg = QueryMessage.objects.create(
            query=self, author=author, body=body, attachment=attachment,
        )
        self.status = (
            QueryStatus.ANSWERED if author_id_is_sales(author, self) else QueryStatus.OPEN
        )
        self.save(update_fields=['status', 'updated_at'])
        return msg

    def close(self):
        self.status = QueryStatus.CLOSED
        self.resolved_at = timezone.now()
        self.save(update_fields=['status', 'resolved_at', 'updated_at'])


def author_id_is_sales(author, query):
    """A reply from the assignee is an answer; from the designer it re-opens."""
    return author_id(author) == author_id(query.assigned_to)


def author_id(user):
    return getattr(user, 'pk', None)


class QueryMessage(TimeStampedModel):
    query = models.ForeignKey(DesignQuery, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='query_messages',
    )
    body = models.TextField()
    attachment = models.FileField(upload_to='queries/', blank=True, null=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.query_id}: {self.body[:30]}'


class MaterialReservation(TimeStampedModel):
    """The two-step material handoff (§10.1·3).

    Design reserves; Stores issues. The production gate reads issued_at, so a
    reservation alone never releases a job to the floor.
    """

    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, related_name='reservations',
    )
    sheet = models.ForeignKey(
        'stores.Sheet', on_delete=models.PROTECT, related_name='reservations',
    )

    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reservations_made',
    )
    reserved_at = models.DateTimeField(auto_now_add=True)

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='materials_issued',
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='materials_consumed',
    )
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-reserved_at']

    def __str__(self):
        return f'{self.sheet.uid} -> {self.so_item}'

    @transaction.atomic
    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating:
            self.sheet.status = SheetStatus.RESERVED
            self.sheet.save(update_fields=['status', 'updated_at'])

    @transaction.atomic
    def issue(self, user):
        """Store in-charge confirms physical handover."""
        if self.issued_at:
            return self
        self.issued_by = user
        self.issued_at = timezone.now()
        self.save(update_fields=['issued_by', 'issued_at', 'updated_at'])
        self.sheet.status = SheetStatus.ISSUED
        self.sheet.save(update_fields=['status', 'updated_at'])
        return self

    @transaction.atomic
    def consume(self, user, leftover=None):
        """Material fully used. `leftover` optionally records a new offcut."""
        self.consumed_at = timezone.now()
        self.consumed_by = user
        self.save(update_fields=['consumed_at', 'consumed_by', 'updated_at'])
        self.sheet.status = SheetStatus.CONSUMED
        self.sheet.save(update_fields=['status', 'updated_at'])
        return self

    @transaction.atomic
    def release(self):
        """Cancelling an order puts the sheet back into available stock."""
        self.released_at = timezone.now()
        self.save(update_fields=['released_at', 'updated_at'])
        self.sheet.status = SheetStatus.AVAILABLE
        self.sheet.save(update_fields=['status', 'updated_at'])


class DesignCheck(TimeStampedModel):
    """One of the six pre-release checks, ticked for one item (spec 07).

    Stored per key rather than as a count, for two reasons: the six checks
    differ by process, so a count means nothing on its own, and "who ticked
    this, and when" is the part that matters when a part comes back wrong.

    A row exists only once the check has been ticked. Unticking deletes it -
    the card asks the model what is ticked, so there is no third state to get
    out of step.
    """

    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, related_name='design_checks')
    key = models.CharField(max_length=30)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='design_checks')
    checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        # One tick per check per item - a double POST must not double count.
        constraints = [
            models.UniqueConstraint(fields=['so_item', 'key'],
                                    name='uniq_design_check_per_item'),
        ]
        ordering = ['key']

    def __str__(self):
        return f'{self.so_item_id}:{self.key}'

    @staticmethod
    def definitions_for(item):
        """[(key, label, why)] for this item's process."""
        return PROCESS_CHECKS[Process(item.process)]

    @classmethod
    def state_for(cls, item):
        """The six checks with their tick state, in the order they are worked."""
        done = {c.key: c for c in item.design_checks.select_related('checked_by')}
        rows = []
        for key, label, why in cls.definitions_for(item):
            hit = done.get(key)
            rows.append({
                'key': key, 'label': label, 'why': why,
                'done': hit is not None,
                'by': hit.checked_by.short_name if hit else '',
                'at': hit.checked_at if hit else None,
            })
        return rows

    @classmethod
    def complete(cls, item):
        """Are all six ticked? Counted against THIS process's keys only.

        Checked against the definition list rather than by counting rows, so a
        part whose process changed after some ticks were made does not read as
        complete on the strength of another process's checks.
        """
        wanted = {k for k, _l, _w in cls.definitions_for(item)}
        ticked = set(item.design_checks.values_list('key', flat=True))
        return wanted.issubset(ticked)

    @classmethod
    def progress(cls, item):
        wanted = [k for k, _l, _w in cls.definitions_for(item)]
        ticked = set(item.design_checks.values_list('key', flat=True))
        return sum(1 for k in wanted if k in ticked), len(wanted)
