"""Forms behind the Order Booking screen (§3.4).

Every "reason" on this screen is mandatory *here*, server-side. The modals also
disable their confirm button until the textarea has content, but that is a
courtesy to the user — it is not the rule. A hand-rolled POST with an empty
reason has to bounce, which is what ReasonForm guarantees.
"""
from decimal import Decimal

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


class RepeatOrderForm(forms.Form):
    """A repeat PO raised by Accounts, without going back to Sales (spec 05).

    The customer sends another PO for a part they have ordered before. The
    rate for that part was approved once and, per the rate-card rule, is
    reused forever without re-approval - so there is nothing new for Sales to
    price and nothing new for the Sales Manager to sign off.

    Accounts therefore picks the customer and the item code exactly as a
    salesperson would. Everything else about the part - material, thickness,
    size, finish, process - belongs to the item code and is shown read-only:
    this screen orders a known part again, it does not redefine one.

    The rate comes from the repeat-rate master, which Accounts maintains: one
    agreed figure per part, no slab ladder. A quotation is banded because it
    prices a range of possible quantities; this prices a known part, so the
    only thing to type when the PO lands is the quantity.

    The rate is not a field on this form at all - the view reads it from the
    master - so no post can price an order.
    """

    customer = forms.IntegerField()
    item_code = forms.IntegerField()
    quantity = forms.IntegerField(min_value=1)
    # No rate field. The view reads it from the repeat-rate master, so there is
    # nothing here for a form post to influence.

    #: Everything the salesperson fills on a quotation except the slab ladder -
    #: a repeat order has one agreed rate instead. quote_date, valid_until and
    #: enquiry_ref are the exceptions: nothing is being quoted here, so there
    #: is no quote to date or to expire, and the PO number is the reference.
    artwork_ref = forms.CharField(max_length=60, required=False)
    tooling_charge = forms.DecimalField(max_digits=10, decimal_places=2,
                                        required=False, min_value=0)

    currency = forms.ChoiceField(required=False)
    payment_terms = forms.CharField(max_length=200, required=False)
    delivery_terms = forms.CharField(max_length=60, required=False)
    lead_time = forms.CharField(max_length=120, required=False)

    tooling_charges = forms.DecimalField(max_digits=10, decimal_places=2,
                                         required=False, min_value=0)
    packing_forwarding = forms.DecimalField(max_digits=10, decimal_places=2,
                                            required=False, min_value=0)
    freight = forms.DecimalField(max_digits=10, decimal_places=2,
                                 required=False, min_value=0)
    gst_percent = forms.DecimalField(max_digits=5, decimal_places=2,
                                     required=False, min_value=0)

    po_number = forms.CharField(max_length=60)
    po_date = forms.DateField()
    expected_delivery = forms.DateField(required=False)
    po_file = forms.FileField()
    #: Required here, unlike the Sales flow. A repeat order goes straight to
    #: Design on confirmation, and Design cannot start without a drawing.
    drawing_file = forms.FileField()
    note = forms.CharField(max_length=250, required=False)

    #: Blank money fields mean nil, not "invalid" - a repeat order with no
    #: freight is the normal case.
    MONEY = ('tooling_charge', 'tooling_charges', 'packing_forwarding', 'freight')

    def __init__(self, *args, **kwargs):
        from core.constants import Currency
        super().__init__(*args, **kwargs)
        self.fields['currency'].choices = Currency.choices
        self.item = None
        self.cust = None
        self.repeat_rate = None

    def clean_gst_percent(self):
        # Blank means the standard rate, not zero tax.
        value = self.cleaned_data.get('gst_percent')
        return Decimal('18.00') if value in (None, '') else value

    def clean(self):
        from masters.models import Customer, ItemCode
        data = super().clean()

        cust_id, code_id = data.get('customer'), data.get('item_code')
        if not cust_id or not code_id:
            return data

        self.cust = Customer.objects.filter(pk=cust_id, is_active=True).first()
        if self.cust is None:
            raise forms.ValidationError('Pick a customer.')

        # Scoped to the customer on purpose: the same code string can exist for
        # two customers and mean two different parts at two different rates.
        self.item = ItemCode.objects.filter(
            pk=code_id, customer=self.cust, is_active=True).first()
        if self.item is None:
            raise forms.ValidationError(
                'That item code does not belong to this customer.')

        rate = getattr(self.item, 'repeat_rate', None)
        if rate is None or not rate.is_active:
            raise forms.ValidationError(
                f'{self.item.code} has no repeat rate on file. Add it to the '
                f'repeat-rate master first, then raise the order.')
        self.repeat_rate = rate

        for name in self.MONEY:
            if data.get(name) in (None, ''):
                data[name] = Decimal('0.00')
        if not data.get('currency'):
            from core.constants import Currency
            data['currency'] = Currency.INR
        return data


class RepeatRateForm(forms.Form):
    """One row of the repeat-rate master: a part and the figure agreed for it.

    The item code may be one of the customer's existing parts or a brand-new
    string, exactly as on the Sales quotation form. A new one is created here
    against this customer, with the spec Accounts fills in - so a part that has
    never been quoted can still be set up for repeat ordering.

    No quantity bands. A repeat order prices a known part at a known rate; the
    quantity arrives with the PO.
    """

    customer = forms.IntegerField()
    item_code_text = forms.CharField(max_length=60)
    description = forms.CharField(max_length=200, required=False)
    material = forms.CharField(max_length=60, required=False)
    thickness = forms.CharField(max_length=30, required=False)
    size = forms.CharField(max_length=60, required=False)
    finish = forms.CharField(max_length=60, required=False)
    process = forms.ChoiceField(required=False)

    rate_per_unit = forms.DecimalField(max_digits=10, decimal_places=2,
                                       min_value=Decimal('0.01'))
    note = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        from core.constants import Process
        super().__init__(*args, **kwargs)
        self.fields['process'].choices = Process.choices
        self.item = None
        self.cust = None
        self.created_code = False

    def clean(self):
        from core.constants import ItemCodeApproval, Process
        from masters.models import Customer, Finish, ItemCode, Material, Thickness
        data = super().clean()

        self.cust = Customer.objects.filter(pk=data.get('customer'), is_active=True).first()
        if self.cust is None:
            raise forms.ValidationError('Pick a customer.')

        text = (data.get('item_code_text') or '').strip()
        if not text:
            raise forms.ValidationError('Pick an item code, or type a new one.')

        # Scoped to the customer: the same string for two customers is two parts.
        code = ItemCode.objects.filter(customer=self.cust, code__iexact=text).first()
        if code is not None:
            self.item = code
            return data

        # A new part. Accounts supplies the spec, since nobody has quoted it.
        self.item = ItemCode(
            customer=self.cust,
            code=text,
            description=data.get('description', ''),
            size=data.get('size', ''),
            material=Material.objects.filter(name=data.get('material')).first(),
            thickness=Thickness.objects.filter(name=data.get('thickness')).first(),
            finish=Finish.objects.filter(name=data.get('finish')).first(),
            process=data.get('process') or Process.ETCHING,
            # PENDING, not APPROVED: Accounts agreeing a repeat rate is not the
            # Sales Manager approving a quoted rate. The code exists so it can
            # be re-ordered; its Sales rate card stays empty until someone
            # quotes it.
            approval_state=ItemCodeApproval.PENDING,
        )
        self.created_code = True
        return data
