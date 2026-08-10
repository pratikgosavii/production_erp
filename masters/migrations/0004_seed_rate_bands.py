"""Turn every single-rate item code into a one-band rate card.

Before this change an ItemCode carried ONE approved_rate for every quantity.
That figure becomes the band 1..(no upper limit), carrying its approval trail
across, so nothing that was priced yesterday reprices today. Codes still
waiting on their first approval get no band at all — there is nothing approved
to carry.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    ItemCode = apps.get_model('masters', 'ItemCode')
    ItemCodeRate = apps.get_model('masters', 'ItemCodeRate')

    bands = []
    for code in ItemCode.objects.exclude(approved_rate=None):
        if code.rate_bands.exists():
            continue
        bands.append(ItemCodeRate(
            item_code=code,
            qty_from=1,
            qty_to=None,
            rate_per_unit=code.approved_rate,
            approved_rate=code.approved_rate,
            proposed_rate=None,
            approval_state='APPROVED',
            approved_via=code.approved_via,
            approved_on=code.approved_on,
            approved_by_id=code.approved_by_id,
            approval_source=code.approval_source,
            revision_reason='',
        ))
    ItemCodeRate.objects.bulk_create(bands)


def backwards(apps, schema_editor):
    """Drop only the bands this migration could have created."""
    ItemCodeRate = apps.get_model('masters', 'ItemCodeRate')
    ItemCodeRate.objects.filter(qty_from=1, qty_to=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('masters', '0003_alter_itemcode_approved_rate_itemcoderate'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
