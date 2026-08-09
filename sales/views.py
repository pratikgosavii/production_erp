"""Sales screens: My Quotations, the quotation form, and the approval queue.

Everything that changes state is a POST followed by a redirect (§9 audit), and
every view is gated by the role flags in core.decorators — hiding a button is
not access control.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.constants import ItemCodeApproval, QuotationStatus
from core.decorators import role_required
from core.services import log, notify
from masters.models import Customer, Finish, ItemCode, Material, Thickness
from sales.forms import (
    BillToForm, BlankTierFormSet, EditTierFormSet, NewTierFormSet, POUploadForm,
    QuotationForm, QuotationItemEditFormSet, QuotationItemFormSet, _thickness_label,
)
from sales.models import Quotation

User = get_user_model()


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
BADGE_CLASS = {
    QuotationStatus.DRAFT: 'badge-gray',
    QuotationStatus.PENDING_APPROVAL: 'badge-amber',
    QuotationStatus.REJECTED: 'badge-red',
    QuotationStatus.APPROVED: 'badge-green',
    QuotationStatus.SENT_TO_CUSTOMER: 'badge-green',
    QuotationStatus.AWAITING_PO: 'badge-green',
    QuotationStatus.PO_UPLOADED: 'badge-green',
    QuotationStatus.PO_REJECTED: 'badge-red',
    QuotationStatus.CONVERTED: 'badge-blue',
}

# Approved / Sent / Awaiting PO are one bucket on the dashboard: the quote is
# out with the customer and the PO has not landed yet.
OUT_WITH_CUSTOMER = {
    QuotationStatus.APPROVED,
    QuotationStatus.SENT_TO_CUSTOMER,
    QuotationStatus.AWAITING_PO,
}


def _user_context(request, role_label):
    return {'user_name': request.user.short_name, 'role_label': role_label}


def _inr(value):
    """1234567 -> '12,34,567'. Indian grouping, used for the credit limit."""
    number = int(Decimal(value or 0))
    text = str(abs(number))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        text = ','.join(groups + [tail])
    return ('-' if number < 0 else '') + text


def _short_money(amount):
    """₹9.4L / ₹63,000 — the summary tiles read better in lakhs."""
    amount = Decimal(amount or 0)
    if amount >= 100000:
        return f'₹{amount / Decimal("100000"):.1f}L'
    return f'₹{_inr(amount)}'


def _spec_line(quotation):
    """'SS 304 · 1.0mm · 150 x 110' from the first line item."""
    items = list(quotation.items.all())
    if not items:
        return '—'
    item = items[0]
    bits = [
        item.material.name if item.material else '',
        item.thickness.name if item.thickness else '',
        item.size,
    ]
    line = ' · '.join(b for b in bits if b) or (item.description or '—')
    if len(items) > 1:
        line += f' (+{len(items) - 1} more)'
    return line


def _owned_quotation(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    if not request.user.is_superuser and quotation.salesperson_id != request.user.pk:
        raise PermissionDenied('That quotation belongs to another salesperson.')
    return quotation


def _accounts_users():
    return User.objects.filter(is_accounts=True, is_active=True)


def _form_error_text(*forms):
    bits = []
    for form in forms:
        for field, errors in form.errors.items():
            label = 'Form' if field == '__all__' else field.replace('_', ' ')
            bits.append(f'{label}: {"; ".join(errors)}')
    return ' — '.join(bits) or 'Please check the highlighted fields.'


# ---------------------------------------------------------------------------
# SCREEN 1 — My Quotations
# ---------------------------------------------------------------------------
FILTER_KEYS = [
    'all', 'pending', 'rejected', 'awaiting_po',
    'po_rejected', 'awaiting_drawing', 'ready', 'draft',
]


def _bucket(quotation):
    """Which dashboard filter chip a quotation belongs to (besides 'all')."""
    status = quotation.status
    if status == QuotationStatus.PO_UPLOADED:
        return 'ready' if quotation.has_drawing else 'awaiting_drawing'
    return {
        QuotationStatus.DRAFT: 'draft',
        QuotationStatus.PENDING_APPROVAL: 'pending',
        QuotationStatus.REJECTED: 'rejected',
        QuotationStatus.APPROVED: 'awaiting_po',
        QuotationStatus.SENT_TO_CUSTOMER: 'awaiting_po',
        QuotationStatus.AWAITING_PO: 'awaiting_po',
        QuotationStatus.PO_REJECTED: 'po_rejected',
    }.get(status, 'converted')


def _action_kind(quotation):
    """Which contextual action the row offers (§3.3)."""
    status = quotation.status
    if status == QuotationStatus.DRAFT:
        return 'draft'
    if status == QuotationStatus.PENDING_APPROVAL:
        return 'pending'
    if status == QuotationStatus.REJECTED:
        return 'rejected'
    if status in OUT_WITH_CUSTOMER:
        return 'awaiting_po'
    if status == QuotationStatus.PO_UPLOADED:
        return 'convertible' if quotation.has_drawing else 'no_drawing'
    if status == QuotationStatus.PO_REJECTED:
        return 'po_rejected'
    return 'converted'


@role_required('is_salesman')
def dashboard(request):
    """My Quotations — every row carries the action its status allows."""
    quotations = (
        Quotation.objects
        .select_related('customer', 'salesperson', 'po_rejected_by')
        .prefetch_related('items__tiers', 'items__item_code')
    )
    if not request.user.is_superuser:
        quotations = quotations.filter(salesperson=request.user)

    search = (request.GET.get('q') or '').strip()
    if search:
        quotations = quotations.filter(customer__name__icontains=search) | \
            quotations.filter(number__icontains=search)

    rows = list(quotations)
    for quotation in rows:
        quotation.bucket = _bucket(quotation)
        quotation.action_kind = _action_kind(quotation)
        quotation.badge_class = BADGE_CLASS.get(quotation.status, 'badge-gray')
        quotation.spec_display = _spec_line(quotation)
        quotation.amount_display = _inr(quotation.grand_total.quantize(Decimal('1')))

    counts = {key: 0 for key in FILTER_KEYS}
    counts['all'] = len(rows)
    for quotation in rows:
        if quotation.bucket in counts:
            counts[quotation.bucket] += 1

    today = timezone.localdate()
    approved_value = sum(
        (q.grand_total for q in rows
         if q.approved_at and q.approved_at.date().replace(day=1) == today.replace(day=1)),
        Decimal('0.00'),
    )

    active = request.GET.get('status') or 'all'
    if active not in FILTER_KEYS:
        active = 'all'
    visible = rows if active == 'all' else [q for q in rows if q.bucket == active]

    context = {
        'quotations': visible,
        'counts': counts,
        'active_filter': active,
        'search': search,
        'total_count': len(rows),
        'approved_count': sum(
            1 for q in rows
            if q.status in OUT_WITH_CUSTOMER or q.status in {
                QuotationStatus.PO_UPLOADED, QuotationStatus.CONVERTED}
        ),
        'approved_value': _short_money(approved_value),
        'today': today,
        'default_delivery': today + timedelta(days=21),
    }
    context.update(_user_context(request, 'Sales'))
    return render(request, 'sales/dashboard.html', context)


@require_POST
@role_required('is_salesman')
def quotation_send(request, pk):
    """Email the approved quote out; the quote now waits on the customer's PO."""
    quotation = _owned_quotation(request, pk)
    if quotation.status not in OUT_WITH_CUSTOMER:
        messages.error(request, f'{quotation.number} is not in a state that can be sent.')
        return redirect('sales:dashboard')

    previous = quotation.status
    quotation.sent_to_customer_at = timezone.now()
    quotation.status = QuotationStatus.AWAITING_PO
    quotation.save(update_fields=['sent_to_customer_at', 'status', 'updated_at'])

    log(request.user, 'Quotation', quotation.number, 'Sent to customer',
        detail=f'Emailed to {quotation.customer.email or quotation.customer.name}',
        from_value=previous, to_value=quotation.status)
    notify(quotation.salesperson,
           f'{quotation.number} sent to {quotation.customer.name} — awaiting PO.',
           link=reverse('sales:dashboard'), object_ref=quotation.number)
    messages.success(
        request,
        f'{quotation.number} sent to {quotation.customer.name}'
        f'{" · " + quotation.customer.email if quotation.customer.email else ""}.',
    )
    return redirect('sales:dashboard')


@require_POST
@role_required('is_salesman')
def quotation_revise(request, pk):
    """Rejected -> a fresh v(n+1) draft, opened straight in the form (§3.3)."""
    quotation = _owned_quotation(request, pk)
    if quotation.status != QuotationStatus.REJECTED:
        messages.error(request, f'{quotation.number} has not been rejected.')
        return redirect('sales:dashboard')

    revision = quotation.create_revision()
    log(request.user, 'Quotation', quotation.number, 'Revision created',
        detail=f'v{quotation.version} -> v{revision.version}',
        from_value=f'v{quotation.version}', to_value=f'v{revision.version}')
    messages.success(request, f'{revision.number} v{revision.version} created — edit and resubmit.')
    return redirect('sales:quotation_edit', pk=revision.pk)


@require_POST
@role_required('is_salesman')
def quotation_po(request, pk):
    """The Upload PO / Drawing modal (§3.3) — a real multipart POST."""
    quotation = _owned_quotation(request, pk)
    form = POUploadForm(request.POST, request.FILES, instance=quotation)
    if not form.is_valid():
        messages.error(request, 'PO not saved — ' + _form_error_text(form))
        return redirect('sales:dashboard')

    was_rejected = quotation.status == QuotationStatus.PO_REJECTED
    previous = quotation.status
    quotation = form.save(commit=False)
    quotation.status = QuotationStatus.PO_UPLOADED
    quotation.po_rejection_reason = ''
    quotation.po_rejected_by = None
    quotation.po_rejected_at = None
    quotation.save()

    log(request.user, 'Quotation', quotation.number,
        'PO re-uploaded' if was_rejected else 'PO uploaded',
        detail=f'PO {quotation.po_number}'
               f'{" with drawing" if quotation.has_drawing else " — NO drawing"}',
        from_value=previous, to_value=quotation.status)
    for user in _accounts_users():
        notify(user, f'PO {quotation.po_number} received for {quotation.number} '
                     f'({quotation.customer.name}).',
               link=reverse('accounts:order_booking'), object_ref=quotation.number)

    if quotation.has_drawing:
        messages.success(request, f'PO saved for {quotation.number} — sent to Accounts.')
    else:
        messages.warning(
            request,
            f'PO saved for {quotation.number}, but no drawing is attached — the job is '
            f'held as "Awaiting drawing" and will not enter the Design queue.',
        )
    return redirect('sales:dashboard')


@require_POST
@role_required('is_salesman')
def quotation_convert(request, pk):
    """Hand the PO to Accounts. The SO number itself is minted there (§1.1)."""
    quotation = _owned_quotation(request, pk)
    if quotation.status != QuotationStatus.PO_UPLOADED:
        messages.error(request, f'{quotation.number} has no PO on file yet.')
        return redirect('sales:dashboard')
    if not quotation.has_drawing:
        messages.error(
            request,
            f'{quotation.number} is blocked — a drawing is required before booking.',
        )
        return redirect('sales:dashboard')

    log(request.user, 'Quotation', quotation.number, 'Sent for order booking',
        detail='PO and drawing complete — queued for Accounts',
        from_value=quotation.status, to_value=quotation.status)
    for user in _accounts_users():
        notify(user, f'{quotation.number} ({quotation.customer.name}) is ready to book.',
               link=reverse('accounts:order_booking'), object_ref=quotation.number)
    messages.success(
        request,
        f'{quotation.number} queued for Accounts — they mint the Sales Order number.',
    )
    # Back to My Quotations: the Order Booking screen belongs to Accounts and a
    # salesperson is not allowed on it.
    return redirect('sales:dashboard')


# ---------------------------------------------------------------------------
# SCREEN 2 — the quotation form (create + edit)
# ---------------------------------------------------------------------------
def _tier_formsets(item_formset, data=None):
    """One nested PriceTier formset per line item, prefixed under its parent."""
    sets = []
    for form in item_formset.forms:
        cls = EditTierFormSet if form.instance.pk else NewTierFormSet
        sets.append(cls(data, instance=form.instance, prefix=f'{form.prefix}-tiers'))
    return sets


def _customer_master():
    rows = []
    for customer in Customer.objects.filter(is_active=True).annotate(nq=Count('quotations')):
        rows.append({
            'id': str(customer.pk),
            'name': customer.name,
            'address': customer.address,
            'gstin': customer.gstin,
            'contact': customer.contact_person,
            'phone': customer.phone,
            'email': customer.email,
            'credit': _inr(customer.credit_limit),
            'city': customer.city,
            'quotes': customer.nq,
        })
    return rows


def _item_code_master():
    """The customer-scoped item-code register, shaped for the type-ahead."""
    data = {str(pk): [] for pk in Customer.objects.filter(is_active=True).values_list('pk', flat=True)}
    codes = (
        ItemCode.objects
        .select_related('material', 'thickness', 'finish')
        .prefetch_related('quote_items__quotation')
    )
    for code in codes:
        quotes = sorted(
            (qi.quotation for qi in code.quote_items.all()),
            key=lambda q: q.quote_date,
        )
        data.setdefault(str(code.customer_id), []).append({
            'code': code.code,
            'desc': code.description or code.code,
            'material': code.material.name if code.material else '',
            'thickness': _thickness_label(code.thickness),
            'size': code.size,
            'finish': code.finish.name if code.finish else '',
            'orders': len(quotes),
            'last': quotes[-1].quote_date.strftime('%d %b %Y') if quotes else '—',
            'active': code.is_active,
            'approved': code.is_approved,
            'rate': float(code.approved_rate) if (code.is_approved and code.approved_rate is not None) else None,
            'qtn': code.approved_via or '—',
            'approvedOn': code.approved_on.strftime('%d %b %Y') if code.approved_on else '—',
            'pendingQtn': quotes[-1].number if quotes else '—',
        })
    return data


def _date_value(bound_field):
    """<input type=date> needs ISO, whether the value is a date or raw POST text."""
    value = bound_field.value()
    if value in (None, ''):
        return ''
    return value.strftime('%Y-%m-%d') if hasattr(value, 'strftime') else str(value)


def _resolve_item_code(customer, code_text, item):
    """An unknown code is created PENDING against this customer (§6 auto-create)."""
    code_text = (code_text or '').strip()
    if not code_text:
        return None
    code = ItemCode.objects.filter(customer=customer, code__iexact=code_text).first()
    if code is None:
        return ItemCode.objects.create(
            customer=customer, code=code_text,
            description=item.description or '',
            material=item.material, thickness=item.thickness,
            size=item.size or '', finish=item.finish,
            approval_state=ItemCodeApproval.PENDING,
        )

    changed = []
    if not code.is_active:
        code.is_active = True
        changed.append('is_active')
    for attr, value in (('material', item.material), ('thickness', item.thickness),
                        ('finish', item.finish)):
        if getattr(code, attr) is None and value is not None:
            setattr(code, attr, value)
            changed.append(attr)
    if not code.size and item.size:
        code.size = item.size
        changed.append('size')
    if changed:
        code.save(update_fields=changed + ['updated_at'])
    return code


def _has_content(item_form, tiers):
    """Is this line item worth saving, or is it just an untouched blank card?"""
    data = item_form.cleaned_data or {}
    if any(data.get(f) for f in ('item_code_text', 'description', 'size', 'quantity')):
        return True
    if data.get('tooling_charge'):
        return True
    dropped = {id(f) for f in tiers.deleted_forms}
    for tier_form in tiers.forms:
        if id(tier_form) in dropped:
            continue
        tier_data = tier_form.cleaned_data or {}
        if tier_data.get('rate_per_unit') or tier_data.get('qty_to'):
            return True
    return False


@transaction.atomic
def _persist(request, form, item_formset, tier_sets, customer, submit):
    quotation = form.save(commit=False)
    quotation.customer = customer
    if not quotation.pk:
        quotation.salesperson = request.user
        quotation.status = QuotationStatus.DRAFT
    quotation.save()

    for form_ in item_formset.deleted_forms:
        if form_.instance.pk:
            form_.instance.delete()

    dropped = {id(f) for f in item_formset.deleted_forms}
    position = 0
    for item_form, tiers in zip(item_formset.forms, tier_sets):
        if id(item_form) in dropped:
            continue
        if not item_form.instance.pk and not _has_content(item_form, tiers):
            # A blank extra card: the spec selects always post their first
            # option, so has_changed() alone would save empty line items.
            continue

        item = item_form.save(commit=False)
        position += 1
        item.position = position
        item.quotation = quotation
        item.item_code = _resolve_item_code(
            customer, item_form.cleaned_data.get('item_code_text'), item,
        )
        if item.item_code and not item.description:
            item.description = item.item_code.description
        item.save()

        tiers.instance = item
        tiers.save()

        # No quantity box on the form: the slab the order is expected to land
        # in is the top of the highest tier, which is what the totals block uses.
        if not item.quantity:
            spans = [t.qty_to or t.qty_from for t in item.tiers.all()]
            item.quantity = max(spans) if spans else 0
            item.save(update_fields=['quantity', 'updated_at'])

    if submit:
        # A changed rate on an already-approved code goes back for sign-off.
        for item in quotation.items.select_related('item_code'):
            code = item.item_code
            if code and code.is_approved and code.approved_rate != item.effective_rate:
                code.propose_revision(item.effective_rate, f'Revised on {quotation.number}')
        quotation.submit()
    return quotation


def _quotation_form(request, quotation):
    if quotation is not None:
        if not request.user.is_superuser and quotation.salesperson_id != request.user.pk:
            raise PermissionDenied('That quotation belongs to another salesperson.')
    editable = quotation is None or quotation.status in QuotationStatus.editable()

    instance = quotation if quotation is not None else Quotation()
    ItemFormSet = QuotationItemEditFormSet if quotation is not None else QuotationItemFormSet
    submitted = False

    if request.method == 'POST':
        if not editable:
            messages.error(
                request,
                f'{quotation.number} is {quotation.get_status_display()} — it can no longer be edited.',
            )
            return redirect('sales:dashboard')

        form = QuotationForm(request.POST, instance=instance)
        bill_form = BillToForm(request.POST)
        item_formset = ItemFormSet(request.POST, instance=instance, prefix='items')
        tier_sets = _tier_formsets(item_formset, request.POST)
        submit = request.POST.get('action') == 'submit'

        typed_name = (request.POST.get('customer_name') or '').strip()
        customer_key = (request.POST.get('customer') or '').strip()

        valid = form.is_valid()
        valid = item_formset.is_valid() and valid
        valid = all(ts.is_valid() for ts in tier_sets) and valid
        valid = bill_form.is_valid() and valid
        if not typed_name and not customer_key.isdigit():
            messages.error(request, 'Pick a customer, or type a new name to create one.')
            valid = False

        if valid:
            customer = _customer_for(customer_key, typed_name, bill_form)
            if customer is None:
                messages.error(request, 'Pick a customer, or type a new name to create one.')
            else:
                saved = _persist(request, form, item_formset, tier_sets, customer, submit)
                if submit:
                    log(request.user, 'Quotation', saved.number, 'Submitted for approval',
                        detail=f'{saved.customer.name} · {saved.grand_total}',
                        from_value=QuotationStatus.DRAFT,
                        to_value=QuotationStatus.PENDING_APPROVAL)
                    for manager in User.objects.filter(is_sales_manager=True, is_active=True):
                        notify(manager,
                               f'{saved.number} v{saved.version} from {request.user.short_name} '
                               f'needs approval.',
                               link=reverse('sales:manager_approvals'),
                               object_ref=saved.number)
                    messages.success(request, f'{saved.number} v{saved.version} submitted for approval.')
                else:
                    log(request.user, 'Quotation', saved.number, 'Draft saved',
                        detail=saved.customer.name)
                    messages.success(request, f'{saved.number} saved as a draft.')
                return redirect('sales:dashboard')
        else:
            messages.error(request, 'Nothing saved — ' + _form_error_text(form, bill_form))
        submitted = True
    else:
        initial = {}
        if quotation is None:
            today = timezone.localdate()
            initial = {
                'quote_date': today,
                'valid_until': today + timedelta(days=30),
                'gst_percent': Decimal('18.00'),
                'payment_terms': '30% Advance, Balance against Docs',
                'delivery_terms': 'Ex-Works',
                'lead_time': '3-4 Weeks from PO + Artwork Approval',
            }
        form = QuotationForm(instance=instance, initial=initial)
        bill_form = BillToForm(instance=quotation.customer if quotation else None)
        item_formset = ItemFormSet(instance=instance, prefix='items')
        tier_sets = _tier_formsets(item_formset)

    # The "+ Add Another Item" template: a real empty_form, placeholders and
    # all, so the JS clone posts field names Django recognises.
    empty_item = item_formset.empty_form
    blank_tiers = BlankTierFormSet(instance=empty_item.instance,
                                   prefix='items-__prefix__-tiers')
    item_rows = [
        {'form': f, 'tiers': ts, 'index': i, 'number': i + 1, 'is_template': False}
        for i, (f, ts) in enumerate(zip(item_formset.forms, tier_sets))
    ]
    item_rows.append({
        'form': empty_item, 'tiers': blank_tiers,
        'index': '__prefix__', 'number': '__prefix__', 'is_template': True,
    })

    customer = quotation.customer if quotation else None
    context = {
        'quotation': quotation,
        'form': form,
        'bill_form': bill_form,
        'item_formset': item_formset,
        'item_rows': item_rows,
        'blank_tiers': blank_tiers,
        'editable': editable,
        'was_submitted': submitted,
        'quote_date_value': _date_value(form['quote_date']),
        'valid_until_value': _date_value(form['valid_until']),
        'materials': Material.objects.filter(is_active=True),
        'thicknesses': [
            {'name': t.name, 'label': _thickness_label(t)}
            for t in Thickness.objects.filter(is_active=True)
        ],
        'finishes': Finish.objects.filter(is_active=True),
        'customer_master': _customer_master(),
        'item_code_master': _item_code_master(),
        'customer_names': {str(c.pk): c.name for c in Customer.objects.filter(is_active=True)},
        'customer_id_value': str(customer.pk) if customer else '',
        'customer_name_value': customer.name if customer else '',
        'delivery_choices': ['Ex-Works', 'FOB', 'CIF'],
    }
    context.update(_user_context(request, 'Sales'))
    return render(request, 'sales/quotation_form.html', context)


def _customer_for(customer_key, typed_name, bill_form):
    """Existing customer by id, else create the typed one on the spot (§3.2)."""
    if customer_key.isdigit():
        existing = Customer.objects.filter(pk=int(customer_key)).first()
        if existing is not None:
            return existing
    if not typed_name:
        return None
    existing = Customer.objects.filter(name__iexact=typed_name).first()
    if existing is not None:
        return existing
    customer = bill_form.save(commit=False) if bill_form.is_bound else Customer()
    customer.pk = None
    customer.name = typed_name
    customer.save()
    return customer


@role_required('is_salesman')
def quotation_create(request):
    return _quotation_form(request, None)


@role_required('is_salesman')
def quotation_edit(request, pk):
    return _quotation_form(request, get_object_or_404(Quotation, pk=pk))


# ---------------------------------------------------------------------------
# SCREEN 3 — Sales Manager approval queue
# ---------------------------------------------------------------------------
def _code_rows(quotation):
    """What approving this quotation would lock into the customer's master."""
    rows = []
    for item in quotation.unapproved_items:
        code = item.item_code
        rows.append({
            'code': code.code,
            'spec': code.spec_line or '—',
            'is_new': code.approved_rate is None,
            'old_rate': code.approved_rate,
            'new_rate': item.effective_rate,
        })
    return rows


def _decorate_for_manager(quotation):
    items = list(quotation.items.all())
    quotation.line_items = items
    quotation.spec_display = _spec_line(quotation)
    quotation.total_qty = sum(i.quantity for i in items)
    quotation.headline_rate = items[0].effective_rate if items else Decimal('0.00')

    rates = [t.rate_per_unit for i in items for t in i.tiers.all()]
    quotation.slab_low = min(rates) if rates else None
    quotation.slab_high = max(rates) if rates else None

    quotation.amount_display = _inr(quotation.grand_total.quantize(Decimal('1')))
    quotation.code_rows = _code_rows(quotation)
    quotation.new_codes = sum(1 for r in quotation.code_rows if r['is_new'])
    quotation.revised_codes = sum(1 for r in quotation.code_rows if not r['is_new'])
    quotation.pending_days = quotation.days_pending or 0
    return quotation


@role_required('is_sales_manager')
def manager_approvals(request):
    """Everything waiting on the Sales Manager (§3.3b)."""
    queue = list(
        Quotation.objects
        .filter(status=QuotationStatus.PENDING_APPROVAL)
        .select_related('customer', 'salesperson', 'supersedes')
        .prefetch_related('items__tiers', 'items__item_code')
        .order_by('submitted_at', 'number')
    )
    for quotation in queue:
        _decorate_for_manager(quotation)

    search = (request.GET.get('q') or '').strip()
    if search:
        needle = search.lower()
        queue = [q for q in queue
                 if needle in q.customer.name.lower() or needle in q.number.lower()]

    high_value = [q for q in queue if q.grand_total > Decimal('50000')]
    resubmitted = [q for q in queue if q.version > 1]
    with_codes = [q for q in queue if q.code_rows]

    active = request.GET.get('filter') or 'all'
    visible = {
        'high': high_value,
        'resubmitted': resubmitted,
        'codes': with_codes,
    }.get(active, queue)

    days = [q.pending_days for q in queue]
    context = {
        'quotations': visible,
        'active_filter': active if active in {'high', 'resubmitted', 'codes'} else 'all',
        'search': search,
        'count_all': len(queue),
        'count_high': len(high_value),
        'count_resubmitted': len(resubmitted),
        'count_codes': len(with_codes),
        'avg_days': round(sum(days) / len(days), 1) if days else 0,
    }
    context.update(_user_context(request, 'Sales Manager'))
    return render(request, 'sales/manager_approvals.html', context)


def _pending_or_redirect(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    if quotation.status != QuotationStatus.PENDING_APPROVAL:
        messages.error(
            request,
            f'{quotation.number} is {quotation.get_status_display()} — it is no longer in the queue.',
        )
        return quotation, False
    return quotation, True


@require_POST
@role_required('is_sales_manager')
def quotation_approve(request, pk):
    """Approving the quote also locks in every new / revised item-code rate."""
    quotation, ok = _pending_or_redirect(request, pk)
    if not ok:
        return redirect('sales:manager_approvals')

    locked = [r['code'] for r in _code_rows(quotation)]
    quotation.approve(request.user)

    log(request.user, 'Quotation', quotation.number, 'Approved',
        detail=('Locked item codes: ' + ', '.join(locked)) if locked else 'No new item codes',
        from_value=QuotationStatus.PENDING_APPROVAL, to_value=QuotationStatus.APPROVED)
    notify(quotation.salesperson,
           f'{quotation.number} v{quotation.version} approved by {request.user.short_name}.',
           link=reverse('sales:dashboard'), object_ref=quotation.number)
    messages.success(
        request,
        f'{quotation.number} approved'
        + (f' — {len(locked)} item code rate(s) locked in.' if locked else '.'),
    )
    return redirect('sales:manager_approvals')


def _send_back(request, pk, comment, action_label):
    quotation, ok = _pending_or_redirect(request, pk)
    if not ok:
        return redirect('sales:manager_approvals')
    if not comment:
        messages.error(request, f'A comment is required to {action_label.lower()}.')
        return redirect('sales:manager_approvals')

    quotation.reject(request.user, comment)
    log(request.user, 'Quotation', quotation.number, action_label, detail=comment,
        from_value=QuotationStatus.PENDING_APPROVAL, to_value=QuotationStatus.REJECTED)
    notify(quotation.salesperson,
           f'{quotation.number} sent back by {request.user.short_name}: {comment[:120]}',
           link=reverse('sales:dashboard'), object_ref=quotation.number)
    messages.success(request, f'{quotation.number} sent back to {quotation.salesperson.short_name}.')
    return redirect('sales:manager_approvals')


@require_POST
@role_required('is_sales_manager')
def quotation_reject(request, pk):
    return _send_back(request, pk, (request.POST.get('comment') or '').strip(), 'Rejected')


@require_POST
@role_required('is_sales_manager')
def quotation_request_revision(request, pk):
    """No REVISION_REQUESTED state exists, so this is a reject with a prefix."""
    comment = (request.POST.get('comment') or '').strip()
    if comment:
        comment = f'Revision requested: {comment}'
    return _send_back(request, pk, comment, 'Revision requested')
