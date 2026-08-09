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

from core.constants import PREFIX_JOB_CARD, Priority, StationStatus
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
                   urgent_reason=''):
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
        card.build_stations()
        return card

    @property
    def total_duration_seconds(self):
        return sum(
            (r.duration_seconds or 0)
            for r in self.stations.filter(status=StationStatus.DONE)
        )

    @transaction.atomic
    def start_work(self, user):
        """Captures the start time; station selection unlocks after this."""
        if self.started_at:
            return self
        self.started_at = timezone.now()
        self.floor_manager = user
        self.save(update_fields=['started_at', 'floor_manager', 'updated_at'])
        return self

    @transaction.atomic
    def build_stations(self, stations=None):
        """Lay out the master list against this card, all pending by default."""
        stations = stations if stations is not None else ProcessStation.objects.filter(is_active=True)
        self.stations.all().delete()
        for idx, station in enumerate(stations, start=1):
            JobCardStation.objects.create(
                job_card=self, station=station, sequence=idx,
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
    def complete_station(self, station_row, user=None):
        """Mark one station done and stamp its duration automatically."""
        station_row.mark_done(user)
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

    class Meta:
        ordering = ['sequence']

    def __str__(self):
        return f'{self.job_card.number} · {self.station.name}'

    @transaction.atomic
    def mark_done(self, user=None):
        """Duration = gap since the previous completion, or since job start."""
        now = timezone.now()
        if not self.started_at:
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
        self.save(update_fields=[
            'started_at', 'completed_at', 'duration_seconds',
            'status', 'operator', 'updated_at',
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
