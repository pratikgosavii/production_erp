"""Forms behind the three Sales screens (§3.2, §3.3).

The templates render every control by hand so the mock-up's markup survives
untouched; these forms therefore exist for validation and for `save()`, not for
widget rendering. The only thing the templates borrow from them is the field
NAME (``bound_field.html_name``), which is what makes the formsets work.
"""
from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory

from masters.models import Customer, Finish, Material, Thickness
from sales.models import PriceTier, Quotation, QuotationItem


def to_decimal(raw, default=Decimal('0.00')):
    """'5,00,000' / '₹1,200' / '' -> Decimal. Never raises."""
    if raw in (None, ''):
        return default
    text = str(raw).replace(',', '').replace('₹', '').replace(' ', '').strip()
    if not text:
        return default
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return default


def lookup_named(model, value, suffixes=()):
    """Resolve a master by its displayed NAME.

    The spec selects post their option text (the type-ahead may inject an
    option that never came from the DB), so matching is by name, and a missing
    master simply resolves to None rather than failing the whole form.
    """
    name = (value or '').strip()
    if not name:
        return None
    obj = model.objects.filter(name__iexact=name).first()
    if obj is not None:
        return obj
    for suffix in suffixes:
        obj = model.objects.filter(name__iexact=name + suffix).first()
        if obj is not None:
            return obj
    return None


# ---------------------------------------------------------------------------
# Quotation header
# ---------------------------------------------------------------------------
class QuotationForm(forms.ModelForm):
    """Everything on the quotation except the customer and the line items."""

    class Meta:
        model = Quotation
        fields = [
            'quote_date', 'valid_until', 'enquiry_ref', 'currency',
            'payment_terms', 'delivery_terms', 'lead_time',
            'tooling_charges', 'packing_forwarding', 'freight', 'gst_percent',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valid_until'].required = False
        for name in ('tooling_charges', 'packing_forwarding', 'freight', 'gst_percent'):
            self.fields[name].required = False

    def _money(self, name, default=Decimal('0.00')):
        value = self.cleaned_data.get(name)
        return default if value in (None, '') else value

    def clean_tooling_charges(self):
        return self._money('tooling_charges')

    def clean_packing_forwarding(self):
        return self._money('packing_forwarding')

    def clean_freight(self):
        return self._money('freight')

    def clean_gst_percent(self):
        return self._money('gst_percent', Decimal('18.00'))


class BillToForm(forms.ModelForm):
    """The Bill-To block. Only used when a brand-new customer is typed in."""

    credit_limit = forms.CharField(required=False)

    class Meta:
        model = Customer
        fields = ['address', 'gstin', 'contact_person', 'phone', 'email', 'credit_limit']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean_credit_limit(self):
        return to_decimal(self.cleaned_data.get('credit_limit'))


# ---------------------------------------------------------------------------
# Line items + their price tiers
# ---------------------------------------------------------------------------
class QuotationItemForm(forms.ModelForm):
    """One line item.

    ``item_code_text`` is free text on purpose: an unknown code is CREATED as a
    PENDING ItemCode against the customer when the quotation is saved (§6), so
    the field cannot be a ModelChoiceField.
    """

    item_code_text = forms.CharField(required=False, max_length=60)
    material = forms.CharField(required=False)
    thickness = forms.CharField(required=False)
    finish = forms.CharField(required=False)
    quantity = forms.IntegerField(required=False, min_value=0)
    tooling_charge = forms.DecimalField(required=False, max_digits=10, decimal_places=2)

    class Meta:
        model = QuotationItem
        fields = [
            'description', 'artwork_ref', 'material', 'thickness', 'finish',
            'size', 'quantity', 'tooling_charge',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance') or self.instance
        if instance is not None and instance.pk:
            # The spec selects are keyed by name, not by pk.
            self.initial['material'] = instance.material.name if instance.material else ''
            self.initial['thickness'] = _thickness_label(instance.thickness)
            self.initial['finish'] = instance.finish.name if instance.finish else ''
            self.initial['item_code_text'] = instance.item_code.code if instance.item_code else ''

    def clean_material(self):
        return lookup_named(Material, self.cleaned_data.get('material'))

    def clean_thickness(self):
        # The select shows '0.5' but the master is named '0.5mm'.
        return lookup_named(Thickness, self.cleaned_data.get('thickness'), suffixes=('mm',))

    def clean_finish(self):
        return lookup_named(Finish, self.cleaned_data.get('finish'))

    def clean_quantity(self):
        return self.cleaned_data.get('quantity') or 0

    def clean_tooling_charge(self):
        value = self.cleaned_data.get('tooling_charge')
        return Decimal('0.00') if value in (None, '') else value


class PriceTierForm(forms.ModelForm):
    """A quantity slab. Every field is optional so a blank row is just ignored."""

    qty_from = forms.IntegerField(required=False, min_value=0)
    qty_to = forms.IntegerField(required=False, min_value=0)
    rate_per_unit = forms.DecimalField(required=False, max_digits=10, decimal_places=2)

    class Meta:
        model = PriceTier
        fields = ['qty_from', 'qty_to', 'rate_per_unit']

    def clean_qty_from(self):
        return self.cleaned_data.get('qty_from') or 0

    def clean_rate_per_unit(self):
        value = self.cleaned_data.get('rate_per_unit')
        return Decimal('0.00') if value in (None, '') else value


def _thickness_label(thickness):
    """'1.5mm' -> '1.5'; the select's option text drops the unit."""
    if thickness is None:
        return ''
    name = thickness.name
    return name[:-2] if name.lower().endswith('mm') else name


QuotationItemFormSet = inlineformset_factory(
    Quotation, QuotationItem, form=QuotationItemForm,
    extra=2, can_delete=True,
)
QuotationItemEditFormSet = inlineformset_factory(
    Quotation, QuotationItem, form=QuotationItemForm,
    extra=0, can_delete=True,
)


def tier_formset_class(extra):
    return inlineformset_factory(
        QuotationItem, PriceTier, form=PriceTierForm, extra=extra, can_delete=True,
    )


# Three blank slabs on a fresh item (mirrors the mock-up), none on a saved one,
# and exactly one inside the JS "add another item" template.
NewTierFormSet = tier_formset_class(3)
EditTierFormSet = tier_formset_class(0)
BlankTierFormSet = tier_formset_class(1)


# ---------------------------------------------------------------------------
# PO / drawing capture (§3.3 — the dashboard modal)
# ---------------------------------------------------------------------------
class POUploadForm(forms.ModelForm):
    """The Upload PO / Drawing modal, as a real multipart POST."""

    class Meta:
        model = Quotation
        fields = [
            'po_number', 'po_file', 'po_date', 'drawing_file',
            'expected_delivery', 'order_type',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['po_number'].required = True
        self.fields['po_date'].required = True
        self.fields['expected_delivery'].required = True
        self.fields['drawing_file'].required = False
        # Re-uploading a drawing alone must not force the PO file again.
        self.fields['po_file'].required = not bool(self.instance and self.instance.po_file)

    def clean_po_file(self):
        return self.cleaned_data.get('po_file') or self.instance.po_file

    def clean_drawing_file(self):
        return self.cleaned_data.get('drawing_file') or self.instance.drawing_file
