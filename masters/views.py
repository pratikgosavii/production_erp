"""Masters screens — Admin only (§6).

Admin is not one of the eight department role flags, so these views cannot use
role_required(). They gate on staff/superuser instead, which is the same test
the Django admin uses.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.constants import ApprovalSource, ItemCodeApproval
from core.services import log

from .forms import CustomerForm, ItemCodeForm, ReviseRateForm
from .models import Customer, Finish, ItemCode, Material, Thickness


def admin_required(view):
    """Masters are maintained by Admin. Everyone else is blocked outright."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_staff or user.is_superuser:
            return view(request, *args, **kwargs)
        raise PermissionDenied('Masters are maintained by Admin only.')
    return wrapper


# ---------------------------------------------------------------- item codes
def _item_code_queryset():
    return ItemCode.objects.select_related(
        'customer', 'material', 'thickness', 'finish',
    ).annotate(
        last_ordered=Max('quote_items__so_items__order__booked_on'),
    )


@admin_required
def item_codes(request):
    codes = _item_code_queryset()

    q = (request.GET.get('q') or '').strip()
    customer_id = (request.GET.get('customer') or '').strip()
    status = (request.GET.get('status') or '').strip()

    shown = codes
    if q:
        shown = shown.filter(
            Q(code__icontains=q) | Q(description__icontains=q)
            | Q(customer__name__icontains=q),
        )
    if customer_id.isdigit():
        shown = shown.filter(customer_id=int(customer_id))
    if status == 'active':
        shown = shown.filter(is_active=True)
    elif status == 'inactive':
        shown = shown.filter(is_active=False)
    elif status == 'pending':
        shown = shown.filter(approval_state=ItemCodeApproval.PENDING)

    total = codes.count()
    return render(request, 'masters/item_codes.html', {
        'codes': shown,
        'summary': {
            'total': total,
            'active': codes.filter(is_active=True).count(),
            'inactive': codes.filter(is_active=False).count(),
            'pending': codes.filter(approval_state=ItemCodeApproval.PENDING).count(),
            'customers': codes.values('customer').distinct().count(),
        },
        'shown_count': shown.count(),
        'total_count': total,
        'customers': Customer.objects.filter(is_active=True),
        'selected_customer': Customer.objects.filter(pk=customer_id).first()
                             if customer_id.isdigit() else None,
        'materials': Material.objects.filter(is_active=True),
        'thicknesses': Thickness.objects.filter(is_active=True),
        'finishes': Finish.objects.filter(is_active=True),
        'q': q, 'customer_id': customer_id, 'status': status,
    })


def _back(request, fallback):
    """Return to the list the action was fired from, filters and all."""
    target = request.POST.get('back') or ''
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback)


def _codes_redirect(request):
    return _back(request, 'masters:item_codes')


@admin_required
@require_POST
def item_code_save(request):
    """+ Add Item Code (born APPROVED, source ADMIN) and Edit."""
    item = None
    if request.POST.get('item'):
        item = get_object_or_404(ItemCode, pk=request.POST['item'])

    form = ItemCodeForm(request.POST, instance=item)
    if not form.is_valid():
        for field, errs in form.errors.items():
            messages.error(request, f'{field}: {" ".join(errs)}')
        return _codes_redirect(request)

    obj = form.save(commit=False)
    if item is None:
        # An Admin-entered code needs no Sales Manager sign-off (§ApprovalSource).
        obj.approval_source = ApprovalSource.ADMIN
        obj.approval_state = ItemCodeApproval.APPROVED
        obj.save()
        obj.approve(form.cleaned_data['rate'], request.user, quotation_no='')
        log(request.user, 'ItemCode', obj.code, 'Added by Admin as approved',
            to_value=str(obj.approved_rate))
        messages.success(
            request, f'{obj.code} added for {obj.customer.name} with an approved rate.')
    else:
        obj.save()
        log(request.user, 'ItemCode', obj.code, 'Item code edited')
        messages.success(request, f'{obj.code} updated. Rate unchanged — use Revise Rate.')
    return _codes_redirect(request)


@admin_required
@require_POST
def item_code_revise(request):
    """Revise Rate -> propose_revision(): back to PENDING with a reason."""
    form = ReviseRateForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            messages.error(request, f'{field}: {" ".join(errs)}')
        return _codes_redirect(request)

    item = form.cleaned_data['item']
    old = item.approved_rate
    item.propose_revision(form.cleaned_data['rate'], form.cleaned_data['reason'])
    log(request.user, 'ItemCode', item.code, 'Rate revision proposed',
        detail=form.cleaned_data['reason'],
        from_value=str(old or ''), to_value=str(form.cleaned_data['rate']))
    messages.success(
        request,
        f'{item.code} sent back for re-approval — the old rate stops auto-filling.',
    )
    return _codes_redirect(request)


@admin_required
@require_POST
def item_code_toggle(request, pk):
    item = get_object_or_404(ItemCode, pk=pk)
    item.is_active = not item.is_active
    item.save(update_fields=['is_active', 'updated_at'])
    log(request.user, 'ItemCode', item.code,
        'Activated' if item.is_active else 'Deactivated')
    messages.success(
        request,
        f'{item.code} {"activated" if item.is_active else "deactivated"}.')
    return _codes_redirect(request)


# ----------------------------------------------------------------- customers
@admin_required
def customers(request):
    year = timezone.localdate().year
    people = Customer.objects.annotate(
        quotation_count=Count('quotations', distinct=True),
        code_count=Count('item_codes', distinct=True),
        year_quotes=Count(
            'quotations', distinct=True,
            filter=Q(quotations__quote_date__year=year),
        ),
    )

    q = (request.GET.get('q') or '').strip()
    city = (request.GET.get('city') or '').strip()
    activity = (request.GET.get('activity') or '').strip()

    shown = people
    if q:
        shown = shown.filter(
            Q(name__icontains=q) | Q(gstin__icontains=q) | Q(city__icontains=q)
            | Q(contact_person__icontains=q) | Q(email__icontains=q)
            | Q(phone__icontains=q),
        )
    if city:
        shown = shown.filter(city=city)
    if activity == '1':
        shown = shown.filter(year_quotes__gt=0)
    elif activity == '0':
        shown = shown.filter(year_quotes=0)

    totals = people.aggregate(credit=Sum('credit_limit'))
    return render(request, 'masters/customers.html', {
        'customers': shown,
        'summary': {
            'total': people.count(),
            'active_year': people.filter(year_quotes__gt=0).count(),
            'credit': totals['credit'] or 0,
            'quotations': sum(c.quotation_count for c in people),
        },
        'shown_count': shown.count(),
        'total_count': people.count(),
        'cities': Customer.objects.exclude(city='').values_list(
            'city', flat=True).order_by('city').distinct(),
        'q': q, 'city': city, 'activity': activity,
        'year': year,
    })


@admin_required
@require_POST
def customer_save(request):
    """+ Add Customer / Edit, with the duplicate-name guard in the form."""
    customer = None
    if request.POST.get('customer'):
        customer = get_object_or_404(Customer, pk=request.POST['customer'])

    form = CustomerForm(request.POST, instance=customer)
    if form.is_valid():
        obj = form.save()
        log(request.user, 'Customer', obj.name,
            'Customer edited' if customer else 'Customer added')
        messages.success(
            request, f'{obj.name} {"updated" if customer else "added"}.')
    else:
        for field, errs in form.errors.items():
            messages.error(request, ' '.join(errs))
    return _back(request, 'masters:customers')
