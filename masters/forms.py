"""Forms for the Admin-only masters screens (§6).

Two rules are enforced here rather than in a view, because both are about the
data and not about the screen: a customer name is unique across the master, and
a rate revision always carries a reason — the Sales Manager reads it when the
code comes back for re-approval.
"""
from decimal import Decimal

from django import forms

from .models import Customer, ItemCode


class ItemCodeForm(forms.ModelForm):
    """+ Add Item Code / Edit.

    Added by Admin the code is born APPROVED — the rate is locked immediately
    and auto-fills on the next quotation. The rate field is only accepted on
    creation; changing an approved rate has to go through Revise Rate so the
    old rate keeps working until the new one is signed off.
    """

    rate = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False,
    )

    class Meta:
        model = ItemCode
        fields = [
            'customer', 'code', 'description', 'material', 'thickness',
            'size', 'finish', 'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].required = True
        self.fields['code'].required = True
        if self.instance.pk is None:
            self.fields['rate'].required = True

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').strip()

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get('customer')
        code = cleaned.get('code')
        if customer and code:
            clash = ItemCode.objects.filter(customer=customer, code__iexact=code)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(
                    'code', f'{code} already exists for {customer.name}.',
                )
        return cleaned


class ReviseRateForm(forms.Form):
    """Approved -> back to PENDING, with the proposal parked alongside."""

    item = forms.ModelChoiceField(queryset=ItemCode.objects.all())
    rate = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    reason = forms.CharField(max_length=250)

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if not reason:
            raise forms.ValidationError(
                'A reason is required — the Sales Manager sees it when approving.',
            )
        return reason

    def clean(self):
        cleaned = super().clean()
        item, rate = cleaned.get('item'), cleaned.get('rate')
        if item and rate is not None and item.approved_rate == rate:
            self.add_error('rate', 'Enter a rate that differs from the current one.')
        return cleaned


class CustomerForm(forms.ModelForm):
    """+ Add Customer / Edit, with the duplicate-name guard the master needs."""

    class Meta:
        model = Customer
        fields = [
            'name', 'address', 'city', 'state', 'pincode', 'gstin',
            'contact_person', 'phone', 'email', 'credit_limit', 'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('address', 'city', 'state', 'pincode', 'gstin',
                     'contact_person', 'phone', 'email', 'credit_limit'):
            self.fields[name].required = False

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        clash = Customer.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(
                f'“{name}” already exists in the customer master.',
            )
        return name

    def clean_credit_limit(self):
        return self.cleaned_data.get('credit_limit') or Decimal('0')

    def clean(self):
        cleaned = super().clean()
        # The screen only asks for an address; the city column is what the
        # filter runs on, so derive it when it was left blank.
        if not (cleaned.get('city') or '').strip():
            tail = (cleaned.get('address') or '').split(',')[-1].strip()
            cleaned['city'] = tail.rstrip('0123456789 ').strip()
        return cleaned
