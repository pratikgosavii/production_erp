"""Forms for the Design screens (§3.6, §7.1, §10.2·3, §10.2·4).

Everything the designer can change goes through one of these, so the same
validation runs whether the POST came from the queue card or the job detail.
Note what is deliberately absent: no price, rate or amount field exists here —
the Design role must never be handed a money value (§2).
"""
from django import forms

from accounts.models import SOItem
from core.constants import SheetStatus
from stores.models import Sheet


class SpecForm(forms.ModelForm):
    """§10.2·4 — holes and drilling detail are typed by the designer."""

    class Meta:
        model = SOItem
        fields = ['holes', 'drilling_detail']


class RevisionUploadForm(forms.Form):
    """A new drawing revision. The revision number is derived, never typed."""

    file = forms.FileField()
    note = forms.CharField(max_length=200, required=False)


class ReserveSheetForm(forms.Form):
    """Pick one AVAILABLE sheet/offcut matching the item's material+thickness.

    The queryset is rebuilt per item so a POST cannot reserve a sheet of the
    wrong spec, or one somebody else already reserved.
    """

    sheet = forms.ModelChoiceField(queryset=Sheet.objects.none())

    def __init__(self, *args, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sheet'].queryset = available_sheets_for(item)


def available_sheets_for(item):
    """Stores inventory filtered to this item's material + thickness."""
    if item is None or item.material is None or item.thickness is None:
        return Sheet.objects.none()
    return (
        Sheet.objects
        .filter(material=item.material, thickness=item.thickness,
                status=SheetStatus.AVAILABLE)
        .select_related('material', 'thickness', 'size', 'rack', 'parent', 'supplier')
        .order_by('is_offcut', 'uid')
    )


class RaiseQueryForm(forms.Form):
    """Routed automatically to the quote's salesperson — no assignee field."""

    subject = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea)


class QueryReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea)
    attachment = forms.FileField(required=False)
