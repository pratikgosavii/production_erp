"""Forms behind the Order Booking screen (§3.4).

Every "reason" on this screen is mandatory *here*, server-side. The modals also
disable their confirm button until the textarea has content, but that is a
courtesy to the user — it is not the rule. A hand-rolled POST with an empty
reason has to bounce, which is what ReasonForm guarantees.
"""
from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


def designer_queryset():
    """The real Design department — §2 roles are boolean flags on the user."""
    return User.objects.filter(is_designer=True, is_active=True).order_by(
        'first_name', 'last_name', 'username',
    )


class BookOrderForm(forms.Form):
    """Accounts confirm the PO; the SO number is minted from this (§1.1).

    Both fields are optional: mint_from_quotation() falls back to the delivery
    date the salesperson captured on the quotation, and a blank designer means
    "auto-assign / not yet allocated" rather than an error.
    """

    expected_delivery = forms.DateField(required=False)
    designer = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False,
    )
    design_note = forms.CharField(required=False, max_length=250)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Bound late so the role list is read at request time, not at import.
        self.fields['designer'].queryset = designer_queryset()


class ReasonForm(forms.Form):
    """A mandatory, audited free-text reason — hold, cancel or PO rejection."""

    reason = forms.CharField(
        required=True,
        max_length=500,
        error_messages={'required': 'A reason is required.'},
    )

    def clean_reason(self):
        # CharField already strips, so a whitespace-only POST fails `required`.
        # This keeps the guarantee explicit for anyone reading the view.
        reason = (self.cleaned_data.get('reason') or '').strip()
        if not reason:
            raise forms.ValidationError('A reason is required.')
        return reason
