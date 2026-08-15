"""Job cards and their stations (§3.8).

A job card covers a SUBSET of item codes — one card, many SOItems, and those
items may even come from different Sales Orders (that is what makes a
"consolidate these five plates into one run" card possible). The membership
lives on JobCardItem; JobCard itself never points at a single item.

The Floor Manager starts the job, ticks which stations apply, then marks each
complete — per-station duration is computed from the gap between marks, never
typed.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.constants import (
    PREFIX_JOB_CARD, Disposition, Priority, RejectionReason, StationStatus,
)
from core.models import Sequence, TimeStampedModel
from masters.models import ProcessStation


class JobCard(TimeStampedModel):
    """§3.8 — minted at 'Send to Production'. Covers one or more SO items."""

    number = models.CharField(max_length=20, unique=True, editable=False)

    priority = models.PositiveSmallIntegerField(
        choices=Priority.choices, default=Priority.P3,
    )

    # A card pulled out of an existing batch because one customer shouted
    # louder. Splitting is allowed, but never silently — the reason is the
    # audit trail for why the rest of the batch slipped.
    is_urgent_split = models.BooleanField(default=False)
    urgent_reason = models.TextField(blank=True)

    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='released_job_cards',
    )
    released_at = models.DateTimeField(null=True, blank=True)

    floor_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='floor_job_cards',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    stations_confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['priority', '-created_at']

    def __str__(self):
        return self.number

    def clean(self):
        super().clean()
        if self.is_urgent_split and not (self.urgent_reason or '').strip():
            raise ValidationError({
                'urgent_reason': 'An urgent split must say why it jumped the queue.',
            })

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number(PREFIX_JOB_CARD, start=500)
        super().save(*args, **kwargs)

    # ---- membership -----------------------------------------------------
    @property
    def so_items(self):
        """The SOItems this card covers, in order-then-position order."""
        return [link.so_item for link in self.items.select_related(
            'so_item', 'so_item__order', 'so_item__quotation_item',
        )]

    @property
    def orders(self):
        """Distinct Sales Orders touched by this card — usually exactly one."""
        from accounts.models import SalesOrder
        return SalesOrder.objects.filter(
            items__job_card_items__job_card=self,
        ).distinct()

    @property
    def primary_order(self):
        """The single order this card belongs to, or None when it spans several."""
        found = list(self.orders[:2])
        return found[0] if len(found) == 1 else None

    @property
    def order_ref(self):
        """A label safe to log/print whether the card spans one order or many."""
        numbers = sorted(o.number for o in self.orders)
        if not numbers:
            return self.number
        if len(numbers) == 1:
            return numbers[0]
        return ' + '.join(numbers)

    @property
    def item_count(self):
        return self.items.count()

    @property
    def total_value(self):
        return sum(
            (link.so_item.quotation_item.line_amount for link in self.items.all()),
            Decimal('0.00'),
        )

    # ---- state ----------------------------------------------------------
    @property
    def is_started(self):
        return self.started_at is not None

    @property
    def planned_qty(self):
        """Pieces this card set out to make — the opening Qty In."""
        return sum(link.qty or 0 for link in self.items.all())

    @property
    def qty_good(self):
        """Good pieces surviving the LAST completed station.

        Not a sum: the same pieces pass through every station, so adding the
        outputs would multiply them. The latest figure is the live count.
        """
        last = (
            self.stations
            .filter(status=StationStatus.DONE)
            .exclude(qty_out__isnull=True)
            .order_by('-sequence')
            .first()
        )
        return last.qty_out if last else self.planned_qty

    @property
    def qty_rejected_total(self):
        """Rejections DO sum — each one is a different piece leaving the line."""
        return sum(r.qty_rejected or 0 for r in self.stations.all())

    @property
    def yield_percent(self):
        started = self.planned_qty
        if not started:
            return 0
        return round(self.qty_good / started * 100)

    @property
    def can_edit(self):
        """Editable only while it is still a plan.

        Once Start Work is pressed the card carries measured durations. Change
        its membership after that and the recorded times describe work at a
        scope that never happened — so editing stops here, though deleting
        does not (that is a deliberate call: see delete_card).
        """
        return self.started_at is None

    @transaction.atomic
    def set_items(self, so_items, user=None):
        """Replace the card's membership. Refuses once work has started."""
        if not self.can_edit:
            return self
        items = list(so_items)
        if not items:
            raise ValueError('A job card must cover at least one item.')

        keep = {i.pk for i in items}
        # Drop the links that are going, add the ones that are new. Orders on
        # BOTH sides have to re-derive: one may have just lost its last item.
        touched = set(self.orders)
        self.items.exclude(so_item_id__in=keep).delete()
        existing = set(self.items.values_list('so_item_id', flat=True))
        for item in items:
            if item.pk not in existing:
                JobCardItem.objects.create(
                    job_card=self, so_item=item, qty=item.quantity or 0,
                )
            touched.add(item.order)

        for order in touched:
            order.refresh_status()
        return self

    @transaction.atomic
    def delete_card(self, user=None):
        """Remove the card and free its items to be carded again.

        Allowed even after Start Work — the Production Manager's call. The
        caller is expected to have logged what is being destroyed FIRST: the
        stations and their timings cascade with the card, so once this returns
        the only surviving record is the audit entry.
        """
        orders = list(self.orders)
        self.delete()
        for order in orders:
            order.refresh_status()

    @property
    def items_awaiting_material(self):
        """Item codes on this card that Stores has not issued material for.

        The §4 material gate lives here, on the floor — not in planning. The
        Production Manager cards and prioritises off the approved drawing; the
        floor is what physically cannot begin without the trolley.
        """
        return [
            link.so_item for link in self.items.select_related('so_item')
            if not link.so_item.material_issued
        ]

    @property
    def can_start(self):
        return not self.items_awaiting_material

    @property
    def stations_selected(self):
        return self.stations.exclude(status=StationStatus.SKIPPED)

    @property
    def applicable_count(self):
        return self.stations_selected.count()

    @property
    def done_count(self):
        return self.stations.filter(status=StationStatus.DONE).count()

    @property
    def skipped_count(self):
        return self.stations.filter(status=StationStatus.SKIPPED).count()

    @property
    def progress_percent(self):
        total = self.applicable_count
        return round(self.done_count / total * 100) if total else 0

    @property
    def is_complete(self):
        total = self.applicable_count
        return bool(total) and self.done_count == total

    @property
    def current_station(self):
        return self.stations.filter(status=StationStatus.IN_PROGRESS).first() \
            or self.stations.filter(status=StationStatus.PENDING).first()

    # ---- transitions ----------------------------------------------------
    @classmethod
    @transaction.atomic
    def create_for(cls, so_items, user, priority=None, is_urgent_split=False,
                   urgent_reason='', descriptions=None):
        """Mint one card covering `so_items` (one, several, or across orders).

        The number does not exist before this call — that is the point of
        'Send to Production'. Priority defaults to the most urgent (lowest
        numeric) priority among the orders the items belong to.
        """
        items = list(so_items)
        if not items:
            raise ValueError('A job card must cover at least one item.')

        urgent_reason = (urgent_reason or '').strip()
        if is_urgent_split and not urgent_reason:
            raise ValidationError({
                'urgent_reason': 'An urgent split must say why it jumped the queue.',
            })

        if priority is None:
            priority = min(item.order.priority for item in items)

        card = cls.objects.create(
            priority=priority,
            is_urgent_split=is_urgent_split,
            urgent_reason=urgent_reason,
            released_by=user if getattr(user, 'is_authenticated', False) else None,
            released_at=timezone.now(),
        )
        for item in items:
            JobCardItem.objects.create(
                job_card=card, so_item=item, qty=item.quantity or 0,
            )
        card.build_stations(descriptions=descriptions)
        return card

    @property
    def total_duration_seconds(self):
        return sum(
            (r.duration_seconds or 0)
            for r in self.stations.filter(status=StationStatus.DONE)
        )

    @transaction.atomic
    def start_work(self, user):
        """Captures the start time; station selection unlocks after this.

        Refuses while any item on the card is still waiting on Stores — this
        is where the §4 material gate is enforced now that planning no longer
        applies it. The caller is expected to have checked `can_start` and to
        report the reason; this guard is the one that cannot be bypassed.
        """
        if not self.can_start:
            return self
        if self.started_at:
            return self
        self.started_at = timezone.now()
        self.floor_manager = user
        self.save(update_fields=['started_at', 'floor_manager', 'updated_at'])
        return self

    @transaction.atomic
    def build_stations(self, stations=None, descriptions=None):
        """Lay out the master list against this card, all pending by default.

        `descriptions` is {station_pk: text} written by the Production Manager
        as he raises the card. It is copied onto the row, so editing it later
        never rewrites the instruction a card already went out with.
        """
        stations = stations if stations is not None else ProcessStation.objects.filter(is_active=True)
        descriptions = descriptions or {}
        self.stations.all().delete()
        for idx, station in enumerate(stations, start=1):
            JobCardStation.objects.create(
                job_card=self, station=station, sequence=idx,
                description=(descriptions.get(station.pk) or '').strip(),
            )

    @transaction.atomic
    def confirm_stations(self, selected_ids):
        """The Floor Manager's tick list — anything unticked is recorded skipped."""
        selected = set(int(i) for i in selected_ids)
        for row in self.stations.all():
            row.status = (
                StationStatus.PENDING if row.pk in selected else StationStatus.SKIPPED
            )
            row.save(update_fields=['status', 'updated_at'])
        self.stations_confirmed_at = timezone.now()
        self.save(update_fields=['stations_confirmed_at', 'updated_at'])

    @transaction.atomic
    def complete_station(self, station_row, user=None, **log_fields):
        """Mark one station done, stamp its duration, record its output."""
        station_row.mark_done(user, **log_fields)
        if self.is_complete and not self.completed_at:
            self.completed_at = timezone.now()
            self.save(update_fields=['completed_at', 'updated_at'])
            # A card can span several orders; every one of them re-derives.
            for order in self.orders:
                order.refresh_status()
        return station_row


class JobCardItem(TimeStampedModel):
    """One item code on one job card (§3.8 — a card covers a subset)."""

    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='items')
    so_item = models.ForeignKey(
        'accounts.SOItem', on_delete=models.CASCADE, related_name='job_card_items',
    )
    qty = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['so_item__order__number', 'so_item__position']
        constraints = [
            models.UniqueConstraint(
                fields=['job_card', 'so_item'], name='uniq_item_per_job_card',
            ),
        ]

    def __str__(self):
        return f'{self.job_card.number} · {self.so_item}'

    @property
    def order(self):
        return self.so_item.order

    @property
    def item_code(self):
        return self.so_item.item_code

    @property
    def line_amount(self):
        return self.so_item.quotation_item.line_amount


class JobCardStation(TimeStampedModel):
    """One station on one job card, with its operator and computed duration."""

    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='stations')
    station = models.ForeignKey(ProcessStation, on_delete=models.PROTECT)
    sequence = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=12, choices=StationStatus.choices, default=StationStatus.PENDING,
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='station_work',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Stored in seconds: flooring to minutes recorded 0 for every station that
    # took under a minute, which is common on the faster stations.
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    remark = models.CharField(max_length=200, blank=True)

    # Written by the Production Manager when he raises the card, NOT held as
    # master data: what the operator needs to know at Chemical Etching depends
    # on the material and thickness of THIS job, so one global instruction per
    # station could never be right for every card.
    description = models.TextField(blank=True)

    # ---- production log (§ yield + rejection tracking) -------------------
    # Counted per station so yield is measured at each step, not only at the
    # end: knowing 3 plates died at Etching is what lets anyone fix Etching.
    qty_in = models.PositiveIntegerField(null=True, blank=True)
    qty_out = models.PositiveIntegerField(null=True, blank=True)
    qty_rejected = models.PositiveIntegerField(default=0)
    rejection_reason = models.CharField(
        max_length=24, choices=RejectionReason.choices, blank=True,
    )
    disposition = models.CharField(
        max_length=12, choices=Disposition.choices, blank=True,
    )

    class Meta:
        ordering = ['sequence']

    def __str__(self):
        return f'{self.job_card.number} · {self.station.name}'

    @property
    def expected_qty_in(self):
        """What walks into this station: the previous station's good output.

        Falls back to the card's planned quantity for the first station, so
        the operator is never asked to work the opening number out himself.
        """
        previous = (
            self.job_card.stations
            .filter(sequence__lt=self.sequence, status=StationStatus.DONE)
            .exclude(qty_out__isnull=True)
            .order_by('-sequence')
            .first()
        )
        if previous is not None:
            return previous.qty_out
        return self.job_card.planned_qty

    @transaction.atomic
    def start(self, user=None):
        """Operator picks the job up. This is what makes the clock honest.

        Before this existed the duration was INFERRED from the gap since the
        previous station finished, which counted every tea break and every
        trolley wait as run time. Now it is measured from the moment work
        actually began.
        """
        if self.started_at or self.status == StationStatus.DONE:
            return self
        self.started_at = timezone.now()
        self.status = StationStatus.IN_PROGRESS
        if user:
            self.operator = user
        self.save(update_fields=[
            'started_at', 'status', 'operator', 'updated_at'])
        return self

    @transaction.atomic
    def mark_done(self, user=None, qty_in=None, qty_out=None, qty_rejected=0,
                  rejection_reason='', disposition=''):
        """Close the station and record what came out of it."""
        now = timezone.now()
        if not self.started_at:
            # Never started explicitly (a legacy card, or Start was skipped):
            # fall back to the old inference rather than recording nothing.
            previous = self.job_card.stations.filter(
                sequence__lt=self.sequence, completed_at__isnull=False,
            ).order_by('-sequence').first()
            self.started_at = (
                previous.completed_at if previous else self.job_card.started_at
            ) or now
        self.completed_at = now
        self.duration_seconds = max(0, int((now - self.started_at).total_seconds()))
        self.status = StationStatus.DONE
        if user:
            self.operator = user

        if qty_in is not None:
            self.qty_in = qty_in
        if qty_out is not None:
            self.qty_out = qty_out
        self.qty_rejected = qty_rejected or 0
        # A reason only means something when something was actually rejected.
        self.rejection_reason = rejection_reason if self.qty_rejected else ''
        self.disposition = disposition if self.qty_rejected else ''

        self.save(update_fields=[
            'started_at', 'completed_at', 'duration_seconds',
            'status', 'operator', 'qty_in', 'qty_out', 'qty_rejected',
            'rejection_reason', 'disposition', 'updated_at',
        ])
        return self

    @property
    def duration_display(self):
        """Human duration — seconds under a minute, otherwise minutes."""
        s = self.duration_seconds
        if s is None:
            return ''
        if s < 60:
            return f'{s} s'
        if s < 3600:
            return f'{s // 60} min'
        return f'{s // 3600}h {(s % 3600) // 60}m'
