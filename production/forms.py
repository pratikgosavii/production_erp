"""Forms for the Production Manager's ready queue and the Floor Manager's card."""
from django import forms

from core.constants import Priority


class PriorityForm(forms.Form):
    """§4 — priority is a plain 1-4 number. High/Normal/Low was dropped."""

    priority = forms.TypedChoiceField(choices=Priority.choices, coerce=int)


class UrgentSplitForm(forms.Form):
    """The urgent/priority-split pair from the queue's selection bar (§3.8).

    Splitting a batch is allowed, but never silently: the checkbox is worthless
    without the reason, so the two are validated together rather than trusting
    the `required` attribute the JavaScript adds to the textarea.
    """

    is_urgent_split = forms.BooleanField(required=False)
    urgent_reason = forms.CharField(required=False, strip=True)

    def clean(self):
        data = super().clean()
        if data.get('is_urgent_split') and not (data.get('urgent_reason') or '').strip():
            raise forms.ValidationError(
                'An urgent / priority split must state a reason.'
            )
        return data


class StationSelectionForm(forms.Form):
    """The Floor Manager's tick list.

    The queryset is bound to one job card so a forged POST cannot confirm
    stations belonging to somebody else's card.
    """

    stations = forms.ModelMultipleChoiceField(queryset=None, required=False)

    def __init__(self, *args, job_card=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stations'].queryset = (
            job_card.stations.all() if job_card else None
        )
