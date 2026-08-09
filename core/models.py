"""Shared infrastructure: timestamps, numbering, audit trail, notifications."""
from django.conf import settings
from django.db import models, transaction


class TimeStampedModel(models.Model):
    """Every business record carries these; nothing else should redefine them."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Sequence(models.Model):
    """Per-prefix counter behind the numbering service (§9).

    A row per prefix (QTN, SO, JC...). Incremented under a row lock so two
    concurrent quotations can never be handed the same number.
    """

    prefix = models.CharField(max_length=10, unique=True)
    last_value = models.PositiveIntegerField(default=0)
    padding = models.PositiveSmallIntegerField(default=4)

    class Meta:
        verbose_name = 'number sequence'

    def __str__(self):
        return f'{self.prefix} @ {self.last_value}'

    @classmethod
    @transaction.atomic
    def next_number(cls, prefix, padding=4, start=0):
        """Return the next formatted number, e.g. 'QTN-0418'.

        select_for_update() is what makes this safe; without it two requests
        read the same last_value and mint duplicate numbers.
        """
        seq, _ = cls.objects.select_for_update().get_or_create(
            prefix=prefix,
            defaults={'last_value': start, 'padding': padding},
        )
        seq.last_value += 1
        seq.save(update_fields=['last_value'])
        return f'{seq.prefix}-{seq.last_value:0{seq.padding}d}'


class AuditLog(TimeStampedModel):
    """Who changed what, when (§9).

    Deliberately denormalised onto strings rather than a GenericForeignKey:
    the log must survive the record it describes being deleted.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_entries',
    )
    object_type = models.CharField(max_length=60)
    object_ref = models.CharField(max_length=40, help_text='Business number, e.g. SO-1042')
    action = models.CharField(max_length=80)
    detail = models.TextField(blank=True)
    from_value = models.CharField(max_length=60, blank=True)
    to_value = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['object_type', 'object_ref'])]

    def __str__(self):
        return f'{self.object_ref}: {self.action}'


class Notification(TimeStampedModel):
    """Handoff alerts between roles (§9)."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications',
    )
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=200, blank=True)
    object_ref = models.CharField(max_length=40, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'-> {self.recipient}: {self.message[:40]}'
