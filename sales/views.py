"""Sales screens: My Quotations, the quotation form, and the approval queue.

Everything that changes state is a POST followed by a redirect (§9 audit), and
every view is gated by the role flags in core.decorators — hiding a button is
not access control.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.constants import ItemCodeApproval, OrderType, QuotationStatus, QueryStatus
from core.decorators import landing_url_name, role_required
from core.models import AuditLog
from core.services import log, notify
from design.models import DesignQuery
from masters.models import Customer, Finish, ItemCode, Material, Thickness
from sales.forms import (
    BillToForm, BlankTierFormSet, EditTierFormSet, NewTierFormSet, POUploadForm,
    QuotationForm, QuotationItemEditFormSet, QuotationItemFormSet, _thickness_label,
)
from sales.models import Quotation
from sales.pdf import build_quotation_pdf, quotation_pdf_filename

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


def _money(value):
    """Same rendering as accounts.views._money — one money style ERP-wide."""
    return f'₹{Decimal(value or 0):,.0f}'


def _rate(value):
    """_money, but per-unit rates keep their paise."""
    return f'₹{Decimal(value or 0):,.2f}'


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


def _assert_can_view(user, quotation):
    """Read access to one quotation — the rule the full-detail screen uses.

    A salesperson sees their own quotations only. Sales Manager, Accounts and
    Admin see every quotation. Both the detail screen and the printable PDF go
    through here, so there is exactly one copy of the rule.
    """
    privileged = user.is_superuser or user.is_sales_manager or user.is_accounts
    if not privileged and quotation.salesperson_id != user.pk:
        raise PermissionDenied('That quotation belongs to another salesperson.')


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
        if quotation.sent_to_accounts_at:
            return 'with_accounts'
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


@role_required('is_salesman', 'is_sales_manager', 'is_accounts')
def quotation_pdf(request, pk):
    """The printable quotation — the document the customer actually receives.

    Inline by default so it opens in a tab; ``?download=1`` forces a save.
    Available whatever the status, a draft included: the salesperson has to be
    able to read the paper before deciding to submit it.
    """
    quotation = get_object_or_404(
        Quotation.objects
        .select_related('customer', 'salesperson')
        .prefetch_related('items__tiers', 'items__item_code',
                          'items__material', 'items__thickness', 'items__finish'),
        pk=pk,
    )
    _assert_can_view(request.user, quotation)

    pdf_bytes = build_quotation_pdf(quotation)
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'{disposition}; filename="{quotation_pdf_filename(quotation)}"'
    )
    return response


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
    if quotation.sent_to_accounts_at:
        messages.info(
            request,
            f'{quotation.number} is already with Accounts — waiting on their PO approval.',
        )
        return redirect('sales:dashboard')
    if not quotation.has_drawing:
        messages.error(
            request,
            f'{quotation.number} is blocked — a drawing is required before booking.',
        )
        return redirect('sales:dashboard')

    quotation.sent_to_accounts_at = timezone.now()
    quotation.save(update_fields=['sent_to_accounts_at', 'updated_at'])

    log(request.user, 'Quotation', quotation.number, 'Sent for order booking',
        detail='PO and drawing complete — queued for Accounts',
        from_value=quotation.status, to_value='WITH_ACCOUNTS')
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
        .prefetch_related('quote_items__quotation', 'rate_bands')
    )
    for code in codes:
        quotes = sorted(
            (qi.quotation for qi in code.quote_items.all()),
            key=lambda q: q.quote_date,
        )
        # The live rate card, so the form can show the salesperson which
        # quantities are already priced before they type a slab across one.
        # Filtered in Python: rate_bands is prefetched above.
        bands = sorted(
            (b for b in code.rate_bands.all()
             if b.approval_state == ItemCodeApproval.APPROVED and b.approved_rate is not None),
            key=lambda b: b.qty_from,
        )
        data.setdefault(str(code.customer_id), []).append({
            'bands': [
                {'from': b.qty_from, 'to': b.qty_to, 'rate': float(b.approved_rate)}
                for b in bands
            ],
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


def _lines_without_quantity(item_formset, tier_sets):
    """Line numbers submitted without a quantity.

    Quantity is typed by the salesperson — it is never inferred from the price
    ladder — and it is what selects both the price tier and the rate band, so a
    quotation cannot be submitted for approval without it.
    """
    dropped = {id(f) for f in item_formset.deleted_forms}
    missing, number = [], 0
    for item_form, tiers in zip(item_formset.forms, tier_sets):
        if id(item_form) in dropped:
            continue
        if not item_form.instance.pk and not _has_content(item_form, tiers):
            continue
        number += 1
        if not (item_form.cleaned_data or {}).get('quantity'):
            missing.append(number)
    return missing


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

    if submit:
        # A slab that half-covers an approved band would put two prices on the
        # same quantity. Refuse the SUBMIT — but keep the draft, because the
        # salesperson is mid-edit and their typing must not be thrown away.
        conflicts = quotation.band_conflict_text
        if conflicts:
            return quotation, conflicts

        # A quantity outside the approved rate card — or one quoted at a
        # different rate — puts THAT BAND back in front of the Sales Manager.
        # Bands the quotation does not touch are left alone.
        for item in quotation.items.select_related('item_code').prefetch_related('tiers'):
            code = item.item_code
            if code is None or not item.needs_code_approval:
                continue
            tier = item.applicable_tier
            if tier is not None:
                code.add_or_revise_band(
                    tier.qty_from, tier.qty_to, item.effective_rate,
                    reason=f'Quoted on {quotation.number}',
                )
            if code.is_approved and code.approved_rate != item.effective_rate:
                code.propose_revision(item.effective_rate, f'Revised on {quotation.number}')
        quotation.submit()
    return quotation, ''


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

        if valid and submit:
            missing = _lines_without_quantity(item_formset, tier_sets)
            if missing:
                messages.error(
                    request,
                    'Quantity is required before a quotation can be submitted — '
                    'item ' + ', '.join(str(n) for n in missing) + ' has none.',
                )
                valid = False

        if valid:
            customer = _customer_for(customer_key, typed_name, bill_form)
            if customer is None:
                messages.error(request, 'Pick a customer, or type a new name to create one.')
            else:
                saved, conflicts = _persist(
                    request, form, item_formset, tier_sets, customer, submit)
                if submit and conflicts:
                    # Kept as a draft on purpose: nothing typed is lost, and
                    # nothing at all has been written to the rate card.
                    log(request.user, 'Quotation', saved.number, 'Submit blocked',
                        detail=f'Overlapping rate band — {conflicts}')
                    messages.error(
                        request,
                        f'{saved.number} was NOT submitted — {conflicts} '
                        f'It has been kept as a draft; correct the slab and submit again.',
                    )
                    return redirect('sales:quotation_edit', pk=saved.pk)
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


def _apply_bill_to(customer, bill_form):
    """Push any Bill-To edits back onto the customer master.

    Only fields the salesperson actually filled in are written, so leaving a
    box empty never blanks a value that is already on file.
    """
    if not bill_form.is_bound or not bill_form.is_valid():
        return []
    changed = []
    for field in bill_form.Meta.fields:
        # Test against the RAW posted value, not the cleaned one: a blank
        # credit limit cleans to Decimal('0'), which would silently zero a
        # real limit that is already on file.
        if not str(bill_form.data.get(field, '')).strip():
            continue
        value = bill_form.cleaned_data.get(field)
        if value in (None, ''):
            continue
        if getattr(customer, field) != value:
            setattr(customer, field, value)
            changed.append(field)
    if changed:
        customer.save(update_fields=changed + ['updated_at'])
    return changed


def _customer_for(customer_key, typed_name, bill_form):
    """Existing customer by id, else create the typed one on the spot (§3.2).

    An existing customer is UPDATED from the Bill-To block, so Sales can fix an
    address or phone number without needing an admin.
    """
    if customer_key.isdigit():
        existing = Customer.objects.filter(pk=int(customer_key)).first()
        if existing is not None:
            _apply_bill_to(existing, bill_form)
            return existing
    if not typed_name:
        return None
    existing = Customer.objects.filter(name__iexact=typed_name).first()
    if existing is not None:
        _apply_bill_to(existing, bill_form)
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
    """Which RATE BANDS approving this quotation would lock into the master."""
    rows = []
    for item in quotation.unapproved_items:
        code = item.item_code
        state = item.band_state
        band = code.band_for(item.quantity)
        tier = item.applicable_tier
        # A straddling slab is neither a new band nor a revision of one — it
        # cannot be written at all, and the manager has to see that BEFORE
        # reaching for Approve.
        clashes = item.band_conflicts
        rows.append({
            'code': code.code,
            'spec': code.spec_line or '—',
            # A revision is the only case with an existing approved figure to
            # show; everything else is a band the rate card does not have yet.
            'is_new': state != 'revised_band',
            'old_rate': band.approved_rate if band else None,
            'new_rate': item.effective_rate,
            'state': state,
            'quantity': item.quantity,
            'band': (f'{max(1, tier.qty_from)}–{tier.qty_to}' if tier and tier.qty_to
                     else f'{max(1, tier.qty_from)}+' if tier else '—'),
            # NEW / REVISION / CONFLICT, for the item-code panel.
            'kind': ('conflict' if clashes
                     else 'revision' if state == 'revised_band' else 'new'),
            'conflict': ' · '.join(
                f'{tier_.range_label} overlaps {other.range_label}'
                for tier_, other in clashes
            ),
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
    quotation.conflict_codes = sum(1 for r in quotation.code_rows if r['kind'] == 'conflict')
    quotation.new_codes = sum(1 for r in quotation.code_rows
                              if r['is_new'] and r['kind'] != 'conflict')
    quotation.revised_codes = sum(1 for r in quotation.code_rows
                                  if not r['is_new'] and r['kind'] != 'conflict')
    quotation.pending_days = quotation.days_pending or 0
    return quotation


@role_required('is_sales_manager')
def manager_quotations(request):
    """Every quotation, whoever raised it — the Sales Manager's history.

    The approval queue only ever shows PENDING_APPROVAL, so once a manager
    approves or rejects something it disappears from their screens entirely.
    This is where they find it again.
    """
    quotations = (
        Quotation.objects
        .select_related('customer', 'salesperson', 'approved_by')
        .prefetch_related('items__tiers')
        .order_by('-quote_date', '-number', '-version')
    )

    status = (request.GET.get('status') or 'all').strip()
    if status != 'all':
        quotations = quotations.filter(status=status)

    q = (request.GET.get('q') or '').strip()
    if q:
        quotations = quotations.filter(
            Q(number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(salesperson__first_name__icontains=q)
        )

    rows = list(quotations[:300])
    for quotation in rows:
        quotation.badge_class = BADGE_CLASS.get(quotation.status, 'badge-gray')
        quotation.spec_display = _spec_line(quotation)
        quotation.total_display = _money(quotation.grand_total)

    # Counts come off the unfiltered set so the chips do not shrink as you use them.
    everything = Quotation.objects.all()
    chips = [('all', 'All', everything.count())] + [
        (value, label, everything.filter(status=value).count())
        for value, label in QuotationStatus.choices
    ]

    context = {
        'rows': rows,
        'chips': [c for c in chips if c[2] or c[0] == status or c[0] == 'all'],
        'active_status': status,
        'search': q,
        'total_shown': len(rows),
    }
    context.update(_user_context(request, 'Sales Manager'))
    return render(request, 'sales/manager_quotations.html', context)


@role_required('is_sales_manager')
def manager_approvals(request):
    """Everything waiting on the Sales Manager (§3.3b)."""
    queue = list(
        Quotation.objects
        .filter(status=QuotationStatus.PENDING_APPROVAL)
        .select_related('customer', 'salesperson', 'supersedes')
        # rate_bands too: the item-code panel compares every typed slab
        # against the customer's live rate card.
        .prefetch_related('items__tiers', 'items__item_code__rate_bands')
        .order_by('-submitted_at', '-number')
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
    try:
        quotation.approve(request.user)
    except ValidationError as exc:
        # Quotation.approve is atomic and refuses before its first write, so
        # the rate card is exactly as it was and the quote stays in the queue.
        log(request.user, 'Quotation', quotation.number, 'Approval blocked',
            detail=' '.join(exc.messages))
        messages.error(request, ' '.join(exc.messages))
        return redirect('sales:manager_approvals')

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


# ---------------------------------------------------------------------------
# SCREEN 4 — Quotation, full detail (read-only)
# ---------------------------------------------------------------------------
# Everything on this screen is derived. The only writes it offers are POSTs to
# the existing action views above, so there is exactly one copy of each rule.
HEADER_BADGE = {status: css.replace('badge-', '') for status, css in BADGE_CLASS.items()}

LOG_TAGS = {
    'Quotation': ('QTN', 'qtn'),
    'SalesOrder': ('SO', 'so'),
    'ItemCode': ('CODE', 'code'),
}

BACK_LABELS = {
    'sales:dashboard': 'My Quotations',
    'sales:manager_approvals': 'Approval Queue',
    'accounts:order_booking': 'Order Booking',
    'admin:index': 'Admin',
}


def _item_spec(item):
    """'SS 304 · 1.0mm · 150 x 110 · Black Epoxy' — blanks dropped."""
    bits = [
        item.material.name if item.material else '',
        item.thickness.name if item.thickness else '',
        item.size,
        item.finish.name if item.finish else '',
    ]
    return ' · '.join(b for b in bits if b)


def _code_badge(item):
    """The rate-band state chip, same vocabulary as the approval queue."""
    if item.item_code is None:
        return None
    return {
        'ok': {'css': 'ok', 'label': 'Approved band rate'},
        'new_band': {'css': 'new', 'label': 'NEW band — pending approval'},
        'revised_band': {'css': 'revised', 'label': 'Band rate revised'},
        'no_slab': {'css': 'revised', 'label': 'No slab covers this quantity'},
    }[item.band_state]


def _tier_range(tier):
    """'101 – 200' / '201 and above' — one label for the ladder and the table."""
    return (f'{tier.qty_from} – {tier.qty_to}' if tier.qty_to
            else f'{tier.qty_from} and above')


def _detail_rows(quotation):
    rows = []
    for item in quotation.items.all():
        applicable = item.applicable_tier
        applicable_pk = applicable.pk if applicable else None
        tiers = [{
            'range': _tier_range(tier),
            'rate': _rate(tier.rate_per_unit),
            'estimated': _money(tier.estimated_amount),
            'applies': tier.pk == applicable_pk,
        } for tier in item.tiers.all()]

        rows.append({
            'pk': item.pk,
            'position': item.position,
            # Blank when no slab covers the quoted quantity — the table says so
            # in amber rather than inventing a rate.
            'slab': _tier_range(applicable) if applicable else '',
            'code': item.item_code.code if item.item_code else '',
            'badge': _code_badge(item),
            'description': item.description,
            'artwork_ref': item.artwork_ref,
            'spec': _item_spec(item),
            'quantity': item.quantity,
            'tiers': tiers,
            'effective_rate': _rate(item.effective_rate),
            'tooling': _money(item.tooling_charge),
            'has_tooling': bool(item.tooling_charge),
            'amount': _money(item.line_amount),
        })
    return rows


def _detail_meta(quotation):
    """Header meta cells — only the ones that actually carry a value."""
    cells = [
        ('Quote Date', quotation.quote_date.strftime('%d %b %Y') if quotation.quote_date else ''),
        ('Valid Until', quotation.valid_until.strftime('%d %b %Y') if quotation.valid_until else ''),
        ('Enquiry Ref', quotation.enquiry_ref),
        ('Currency', quotation.currency),
        ('Order Type', OrderType(quotation.order_type).label if quotation.order_type else ''),
        ('Submitted', timezone.localtime(quotation.submitted_at).strftime('%d %b %Y, %I:%M %p')
         if quotation.submitted_at else ''),
        ('Days Pending', f'{quotation.days_pending}d'
         if quotation.days_pending is not None else ''),
        ('Expected Delivery',
         quotation.expected_delivery.strftime('%d %b %Y') if quotation.expected_delivery else ''),
    ]
    return [{'label': label, 'value': value} for label, value in cells if value]


def _detail_totals(quotation):
    rows = [
        {'label': 'Sub Total', 'value': _money(quotation.sub_total)},
    ]
    if quotation.tooling_charges:
        rows.append({'label': 'Tooling Charges', 'value': _money(quotation.tooling_charges)})
    if quotation.packing_forwarding:
        rows.append({'label': 'Packing & Forwarding',
                     'value': _money(quotation.packing_forwarding)})
    if quotation.freight:
        rows.append({'label': 'Freight', 'value': _money(quotation.freight)})
    rows.append({'label': 'Taxable Amount', 'value': _money(quotation.taxable_amount)})
    gst = Decimal(quotation.gst_percent or 0).normalize()
    rows.append({'label': f'GST @ {gst}%', 'value': _money(quotation.gst_amount)})
    return rows


def _detail_activity(quotation):
    entries = (
        AuditLog.objects
        .filter(object_ref=quotation.number)
        .select_related('actor')[:60]
    )
    rows = []
    for entry in entries:
        tag, css = LOG_TAGS.get(entry.object_type, (entry.object_type[:6].upper(), 'so'))
        rows.append({
            'tag': tag,
            'tag_class': css,
            'action': entry.action,
            'detail': entry.detail,
            'actor': entry.actor.short_name if entry.actor else 'System',
            'created_at': entry.created_at,
        })
    return rows


@role_required('is_salesman', 'is_sales_manager', 'is_accounts')
def quotation_detail(request, pk):
    """Quotation — Full Detail. Read-only apart from the contextual actions."""
    quotation = get_object_or_404(
        Quotation.objects
        .select_related('customer', 'salesperson', 'supersedes',
                        'approved_by', 'po_rejected_by')
        .prefetch_related('items__tiers', 'items__item_code__rate_bands',
                          'items__material', 'items__thickness', 'items__finish'),
        pk=pk,
    )

    user = request.user
    _assert_can_view(user, quotation)

    is_manager = user.is_sales_manager or user.is_superuser
    owns = quotation.salesperson_id == user.pk
    can_act_as_owner = (user.is_salesman and owns) or user.is_superuser

    role_label = ('Sales Manager' if user.is_sales_manager else
                  'Accounts' if user.is_accounts else
                  'Sales' if user.is_salesman else 'Admin')
    # Breadcrumb points back at whichever list this role actually owns.
    landing = landing_url_name(user)

    # Once booked, the header links across to the Sales Order — but only for
    # the roles accounts.views.order_detail actually admits. A salesperson is
    # not one of them, so they would only meet a 403.
    sales_order = quotation.sales_orders.first()
    can_open_sales_order = sales_order is not None and (
        user.is_superuser or user.is_accounts
        or user.is_sales_manager or user.is_production_manager
    )

    context = {
        'quotation': quotation,
        'badge_class': HEADER_BADGE.get(quotation.status, 'gray'),
        'grand_total': _money(quotation.grand_total),
        'meta': _detail_meta(quotation),
        'credit_limit': _money(quotation.customer.credit_limit),
        'rows': _detail_rows(quotation),
        # The item-master table foots on the line-item sub total, not the
        # grand total — charges and GST sit below it in the Totals card.
        'items_total': _money(quotation.sub_total),
        'totals': _detail_totals(quotation),
        'code_rows': _code_rows(quotation),
        'activity': _detail_activity(quotation),
        # Contextual actions — every one of them POSTs to an existing view.
        'can_approve': is_manager and quotation.status == QuotationStatus.PENDING_APPROVAL,
        'can_edit': can_act_as_owner and quotation.status in QuotationStatus.editable(),
        # The quote is out with the customer — the only thing left for Sales to
        # do is record the PO when it arrives.
        'can_upload_po': can_act_as_owner and quotation.status in OUT_WITH_CUSTOMER,
        'is_manager': is_manager,
        'sales_order': sales_order,
        'can_open_sales_order': can_open_sales_order,
        'back_url': reverse(landing),
        'back_label': BACK_LABELS.get(landing, 'Back'),
        'today': timezone.localdate(),
        'default_delivery': timezone.localdate() + timedelta(days=21),
    }
    context.update(_user_context(request, role_label))
    return render(request, 'sales/quotation_detail.html', context)


# ---------------------------------------------------------------------------
# SCREEN 5 — Design Queries (the salesperson's inbox, §7.1)
# ---------------------------------------------------------------------------
# DesignQuery.raise_for() routes a query to the exact salesperson who raised the
# originating quotation and halts the job. This is where that person finally
# sees it. Closing is deliberately absent: only the designer can judge whether
# the answer actually unblocks the drawing, so only Design may close.
QUERY_FILTERS = ['open', 'answered', 'closed', 'all']

# Open first — those are the ones holding a job up. Within a bucket the
# queryset's own '-created_at' ordering (newest first) is preserved.
QUERY_RANK = {QueryStatus.OPEN: 0, QueryStatus.ANSWERED: 1, QueryStatus.CLOSED: 2}


def _my_queries(user):
    """Queries assigned to this user — a superuser inspects every inbox."""
    queries = (
        DesignQuery.objects
        .select_related('order__customer', 'order__quotation',
                        'so_item__quotation_item__item_code',
                        'raised_by', 'assigned_to')
        .prefetch_related('messages__author')
    )
    if not user.is_superuser:
        queries = queries.filter(assigned_to=user)
    return queries


def _query_item_label(query):
    """Which item code the query concerns; so_item is null for order-level."""
    if query.so_item is None:
        return ''
    code = query.so_item.item_code
    return code.code if code else f'Item {query.so_item.position}'


def _query_thread(query):
    """The full conversation, mirroring the Design-side thread markup.

    A message from the assignee is the Sales side; anything else came from
    Design — exactly the rule DesignQuery.reply() uses to flip the status.
    """
    rows = []
    for msg in query.messages.all():
        is_sales = msg.author_id == query.assigned_to_id
        rows.append({
            'author': msg.author.short_name,
            'role_label': 'Sales' if is_sales else 'Design',
            'side': 'sales' if is_sales else 'design',
            'created_at': msg.created_at,
            'body': msg.body,
            'attachment': msg.attachment if msg.attachment else None,
        })
    return rows


def _decorate_query(query):
    query.item_label = _query_item_label(query)
    query.pill_class = query.status.lower()
    query.thread = _query_thread(query)
    query.can_reply = query.status != QueryStatus.CLOSED
    # The SO itself lives on an Accounts screen a salesperson may not GET, so
    # the number links to the quotation it came from — which they own.
    query.quotation_url = reverse('sales:quotation_detail', args=[query.order.quotation_id])
    return query


def _owned_query(request, pk):
    query = get_object_or_404(_my_queries(request.user), pk=pk)
    return query


@role_required('is_salesman')
def design_queries(request):
    """My Design Queries — the inbox §7.1 promised and never built."""
    rows = list(_my_queries(request.user))

    counts = {'all': len(rows), 'open': 0, 'answered': 0, 'closed': 0}
    for query in rows:
        key = query.status.lower()
        if key in counts:
            counts[key] += 1

    # Default lands on the ones needing him, not on everything.
    active = (request.GET.get('status') or 'open').lower()
    if active not in QUERY_FILTERS:
        active = 'open'

    visible = rows if active == 'all' else [q for q in rows if q.status.lower() == active]
    visible.sort(key=lambda q: QUERY_RANK.get(q.status, 3))
    for query in visible:
        _decorate_query(query)

    context = {
        'queries': visible,
        'counts': counts,
        'active_filter': active,
        'has_any': bool(rows),
    }
    context.update(_user_context(request, 'Sales'))
    return render(request, 'sales/queries.html', context)


@require_POST
@role_required('is_salesman')
def query_reply(request, pk):
    """Answer the designer. reply() flips the query to ANSWERED for us."""
    query = _owned_query(request, pk)
    body = (request.POST.get('body') or '').strip()

    if query.status == QueryStatus.CLOSED:
        messages.error(request, 'That query has been closed by Design — nothing was posted.')
        return redirect('sales:queries')
    if not body:
        messages.error(request, 'Type a reply before sending.')
        return redirect('sales:queries')

    previous = query.status
    query.reply(request.user, body)

    item = _query_item_label(query) or 'order-level'
    log(request.user, 'DesignQuery', query.order.number, 'Query answered by Sales',
        detail=f'{item} · {query.subject}',
        from_value=previous, to_value=query.status)
    # Straight back to the designer's own job screen, on the right item.
    link = reverse('design:job_detail', args=[query.order.number])
    if query.so_item_id:
        link = f'{link}?item={query.so_item_id}'
    notify(query.raised_by,
           f'{request.user.short_name} answered your query on {query.order.number}: '
           f'{query.subject}',
           link=link, object_ref=query.order.number)
    messages.success(
        request,
        f'Reply sent to {query.raised_by.short_name} — {query.order.number} is marked Answered. '
        f'Design decides whether it unblocks the job.',
    )
    return redirect(f"{reverse('sales:queries')}?status={request.POST.get('back') or 'open'}")
