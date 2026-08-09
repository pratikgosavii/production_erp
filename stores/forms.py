"""Forms behind the Stores screens (§3.9).

Every dropdown is bound to a masters table so Stores can never invent a second
spelling of "Rack B-3". Finish is the one exception: the shop genuinely does
get one-off finishes, so the select carries an ``__other`` escape option and is
resolved by hand rather than being a plain ModelChoiceField.

Status is deliberately absent from every form here — it is driven by the
workflow (Design reserves, Stores issues, Production consumes), never typed.
"""
from django import forms
from django.utils import timezone

from masters.models import Finish, Grade, Material, Rack, SheetSize, Supplier, Thickness

from .models import Sheet

MAX_BATCH = 16

# A blank QR label has no material yet, but Sheet.material/thickness are NOT
# NULL. Rather than loosen the model we park those sheets on a placeholder
# master row that is flagged inactive, so it never appears in any dropdown and
# the screens can render it as "unassigned".
UNASSIGNED = 'Unassigned'

# Sentinel value of the "Other (type)…" option on the finish select.
OTHER = '__other'


def unassigned_material():
    return Material.objects.get_or_create(
        name=UNASSIGNED, defaults={'is_active': False},
    )[0]


def unassigned_thickness():
    return Thickness.objects.get_or_create(
        name=UNASSIGNED, defaults={'is_active': False},
    )[0]


def active_lookups():
    """The querysets every sheet form and template renders its selects from."""
    return {
        'materials': Material.objects.filter(is_active=True),
        'thicknesses': Thickness.objects.filter(is_active=True),
        'grades': Grade.objects.filter(is_active=True),
        'sizes': SheetSize.objects.filter(is_active=True),
        'finishes': Finish.objects.filter(is_active=True),
        'suppliers': Supplier.objects.filter(is_active=True),
        'racks': Rack.objects.filter(is_active=True),
    }


class SheetFormBase(forms.ModelForm):
    """Shared master-data plumbing for the two sheet forms.

    ``finish`` is declared as a plain CharField and kept out of Meta.fields so
    the ModelForm machinery never tries to assign the raw select value — which
    may be the ``__other`` sentinel — straight onto the FK.
    """

    finish = forms.CharField(required=False)

    REQUIRED = ('material', 'thickness', 'grade', 'size', 'supplier', 'rack',
                'purchase_date')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lookups = active_lookups()
        for name, key in (
            ('material', 'materials'), ('thickness', 'thicknesses'),
            ('grade', 'grades'), ('size', 'sizes'),
            ('supplier', 'suppliers'), ('rack', 'racks'),
        ):
            if name in self.fields:
                self.fields[name].queryset = lookups[key]
        for name in self.REQUIRED:
            if name in self.fields:
                self.fields[name].required = True

    def clean_finish(self):
        raw = (self.cleaned_data.get('finish') or '').strip()
        if not raw or raw == OTHER:
            return None
        try:
            return Finish.objects.get(pk=int(raw))
        except (Finish.DoesNotExist, TypeError, ValueError):
            raise forms.ValidationError('Pick a finish from the list.')

    def clean(self):
        cleaned = super().clean()
        typed = (cleaned.get('finish_other') or '').strip()
        if self.data.get('finish') == OTHER and not typed:
            self.add_error(
                'finish_other', 'Type the finish name, or pick one from the list.',
            )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.finish = self.cleaned_data.get('finish')
        if commit:
            obj.save()
        return obj


class BlankBatchForm(forms.Form):
    """Mode A — reserve N sheet UIDs now, fill the details in on scan."""

    quantity = forms.IntegerField(min_value=1, max_value=MAX_BATCH, initial=8)

    def create_sheets(self, user, label_batch):
        material = unassigned_material()
        thickness = unassigned_thickness()
        today = timezone.localdate()
        return [
            Sheet.objects.create(
                material=material, thickness=thickness,
                received_on=today, logged_by=user, label_batch=label_batch,
            )
            for _ in range(self.cleaned_data['quantity'])
        ]


class SheetLogForm(SheetFormBase):
    """Mode B — full entry; the sheets enter stock as Available at once."""

    quantity = forms.IntegerField(min_value=1, max_value=MAX_BATCH, initial=4)

    class Meta:
        model = Sheet
        fields = [
            'material', 'thickness', 'grade', 'size', 'finish_other',
            'supplier', 'rack', 'purchase_date',
        ]

    def create_sheets(self, user, label_batch):
        cd = self.cleaned_data
        today = timezone.localdate()
        return [
            Sheet.objects.create(
                material=cd['material'], thickness=cd['thickness'],
                grade=cd['grade'], size=cd['size'],
                finish=cd.get('finish'), finish_other=cd.get('finish_other', ''),
                supplier=cd['supplier'], rack=cd['rack'],
                purchase_date=cd['purchase_date'],
                received_on=cd['purchase_date'] or today,
                logged_by=user, label_batch=label_batch,
            )
            for _ in range(cd['quantity'])
        ]


class SheetDetailForm(SheetFormBase):
    """The editable half of the sheet screen. Status is not on it, by design."""

    class Meta:
        model = Sheet
        fields = [
            'material', 'thickness', 'grade', 'size', 'finish_other',
            'supplier', 'rack', 'purchase_date', 'remarks', 'document',
        ]
