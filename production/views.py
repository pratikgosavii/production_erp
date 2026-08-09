"""Production ready queue and the shop-floor job card (§3.8, §4, §10.1·3).

The gate this module defends is the one from §4: a job reaches the floor only
when the drawing is approved AND the material has been *issued* by Stores.
A reservation is not enough, so readiness is read off SOItem.is_ready, which
asks for a live reservation carrying an issued_at stamp.

The queue is ITEM level, not order level. Every item code of every live order
is listed — ready ones selectable, blocked ones greyed with their own reason —
because a job card covers a subset of item codes and may even span orders.
Selection is a courtesy; `create_job_card` re-checks every gate server-side.

The job card enforces its own ordering server-side — Start Work, then confirm
the station list, then mark stations complete. Disabled buttons are a courtesy;
the checks below are the rule.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import SOItem
from core.constants import OrderStatus, Priority, StationStatus
from core.decorators import role_required
from core.services import log, notify

from .forms import PriorityForm, StationSelectionForm, UrgentSplitForm
from .models import JobCard, JobCardStation

# Colour bands for the Due In column.
DUE_CRITICAL_DAYS = 2
DUE_SOON_DAYS = 7

# Orders that are not on the shop floor's radar at all.
DEAD_ORDER_STATUSES = [OrderStatus.CANCELLED, OrderStatus.ON_HOLD]

RECENT_JOB_CARDS = 10


def _due_bucket(days):
    if days is None:
        return 'ok'
    if days <= DUE_CRITICAL_DAYS:
        return 'crit'
    if days <= DUE_SOON_DAYS:
        return 'soon'
    return 'ok'


def _queue_items():
    """Every item code still waiting for a job card, ready or blocked.

    Blocked items are deliberately NOT filtered out — the planner needs to see
    what is missing from an order as much as what is available.
    """
    return (
        SOItem.objects
        .filter(job_card_items__isnull=True)
        .exclude(order__status__in=DEAD_ORDER_STATUSES)
        .select_related(
            'order', 'order__customer', 'order__quotation',
            'quotation_item', 'quotation_item__item_code',
            'quotation_item__material', 'quotation_item__thickness',
            'quotation_item__finish',
        )
        .prefetch_related(
            'reservations', 'queries', 'drawing_revisions',
            'quotation_item__tiers', 'order__queries',
        )
        .order_by('order__priority', 'order__number', 'position')
    )


def _item_code_text(item):
    """A printable item code — falls back to the line description."""
    code = item.quotation_item.item_code
    if code is not None:
        return code.code
    return item.quotation_item.description or f'Item #{item.position}'


def _spec_text(item):
    """Material · thickness · size · finish, skipping whatever is blank."""
    qi = item.quotation_item
    bits = [
        qi.material.name if qi.material else '',
        qi.thickness.name if qi.thickness else '',
        qi.size,
        qi.finish.name if qi.finish else '',
    ]
    return ' · '.join(b for b in bits if b) or '—'


def _job_card_state(card):
    """A one-word state for the results list — JobCard stores no status field."""
    if card.is_complete:
        return 'Completed'
    if card.stations_confirmed_at:
        return 'In production'
    if card.is_started:
        return 'Selecting stations'
    return 'Assigned'


@role_required('is_production_manager')
def dashboard(request):
    """Item-level ready queue. No KPI tiles — those were dropped by design."""
    today = timezone.localdate()

    groups = {}
    for item in _queue_items():
        order = item.order
        days = (order.expected_delivery - today).days if order.expected_delivery else None
        ready = item.is_ready

        group = groups.get(order.pk)
        if group is None:
            group = groups[order.pk] = {
                'order': order,
                'days': days,
                'due_class': _due_bucket(days),
                'rows': [],
                'ready_count': 0,
            }

        group['rows'].append({
            'item': item,
            'order': order,
            'code': _item_code_text(item),
            'spec': _spec_text(item),
            'qty': item.quantity,
            'value': item.quotation_item.line_amount,
            'is_ready': ready,
            # blocked_reason is the item's own words; never re-worded here.
            'reason': '' if ready else (item.blocked_reason or 'Not ready'),
            'days': days,
            'due_class': _due_bucket(days),
        })
        if ready:
            group['ready_count'] += 1

    groups = list(groups.values())

    cards = (
        JobCard.objects
        .prefetch_related(
            'items__so_item__quotation_item__item_code',
            'items__so_item__order',
            'stations',
        )
        .order_by('-created_at')[:RECENT_JOB_CARDS]
    )
    recent_cards = [{
        'card': card,
        'state': _job_card_state(card),
        'orders': ', '.join(sorted(o.number for o in card.orders)) or '—',
        'codes': [_item_code_text(link.so_item) for link in card.items.all()],
    } for card in cards]

    total_rows = sum(len(g['rows']) for g in groups)
    return render(request, 'production/dashboard.html', {
        'groups': groups,
        'total_rows': total_rows,
        'ready_total': sum(g['ready_count'] for g in groups),
        'blocked_total': total_rows - sum(g['ready_count'] for g in groups),
        'priorities': Priority.choices,
        'recent_cards': recent_cards,
    })


@role_required('is_production_manager')
@require_POST
def set_priority(request):
    """Priority lives on the SO — it is set before any job card exists (§4).

    The queue renders one control per order group and posts `order`; the older
    per-item callers post `item`. Both still land on the same sales order.
    """
    if request.POST.get('order'):
        from accounts.models import SalesOrder
        order = get_object_or_404(SalesOrder, pk=request.POST.get('order'))
    else:
        order = get_object_or_404(SOItem, pk=request.POST.get('item')).order

    form = PriorityForm(request.POST)
    if form.is_valid():
        was = order.priority
        order.priority = form.cleaned_data['priority']
        order.save(update_fields=['priority', 'updated_at'])
        log(request.user, 'SalesOrder', order.number, 'Priority changed',
            from_value=was, to_value=order.priority)
    return redirect('production:dashboard')


@role_required('is_production_manager')
@require_POST
def create_job_card(request):
    """Mint ONE job card covering the ticked item codes (§3.8).

    Everything the template disabled is re-checked here: a hand-rolled POST
    must not be able to put a blocked item, or an item already on a card, onto
    the floor. Any rejection changes nothing at all.
    """
    raw_ids = request.POST.getlist('items')

    def refuse(text):
        messages.error(request, text)
        return redirect('production:dashboard')

    try:
        pks = {int(raw) for raw in raw_ids}
    except (TypeError, ValueError):
        return refuse('That selection could not be read. Nothing was created.')

    if not pks:
        return refuse('Select at least one ready item code before creating a job card.')

    split = UrgentSplitForm(request.POST)
    if not split.is_valid():
        return refuse(
            '; '.join(split.errors.get('__all__', ['That selection could not be read.']))
            + ' No job card was created.'
        )
    is_urgent = split.cleaned_data['is_urgent_split']
    reason = split.cleaned_data['urgent_reason'].strip()

    found = list(
        SOItem.objects
        .filter(pk__in=pks)
        .select_related('order', 'quotation_item', 'quotation_item__item_code')
    )
    if len(found) != len(pks):
        return refuse('Some selected item codes no longer exist. Nothing was created.')

    on_card, blocked = [], []
    for item in found:
        if item.job_card_items.exists():
            on_card.append(f'{_item_code_text(item)} ({item.current_job_card})')
        elif item.order.status in DEAD_ORDER_STATUSES:
            blocked.append(
                f'{_item_code_text(item)} — {item.order.get_status_display()}'
            )
        elif not item.is_ready:
            blocked.append(
                f'{_item_code_text(item)} — {item.blocked_reason or "not ready"}'
            )

    if on_card:
        return refuse(
            'Already on a job card, so nothing was created: ' + '; '.join(on_card)
        )
    if blocked:
        return refuse(
            'These item codes are not ready, so nothing was created: '
            + '; '.join(blocked)
        )

    try:
        with transaction.atomic():
            card = JobCard.create_for(
                found, request.user,
                is_urgent_split=is_urgent, urgent_reason=reason,
            )
            orders = list(card.orders)
            for order in orders:
                order.refresh_status()
    except (ValidationError, ValueError) as exc:
        return refuse(f'Job card not created: {exc}')

    codes = ', '.join(sorted(_item_code_text(i) for i in found))
    log(request.user, 'JobCard', card.order_ref,
        f'{card.number} released to production',
        detail=f'{len(found)} item code(s): {codes}'
               + (f' · Urgent split — {reason}' if is_urgent else ''),
        to_value=card.number)
    for order in orders:
        notify(order.assigned_designer,
               f'{card.number} released for {order.number}',
               object_ref=order.number)

    messages.success(
        request,
        f'{card.number} created covering {len(found)} item code'
        f'{"" if len(found) == 1 else "s"} ({codes}).',
    )
    return redirect('production:job_card', jc_no=card.number)


@role_required('is_production_manager')
@require_POST
def send_to_production(request):
    """Mint the job card here — the JC number does not exist before this POST.

    One card can cover many item codes; this screen still releases a single
    item at a time, so it hands `create_for` a one-element list. The
    multi-select queue is a separate piece of work.
    """
    item = get_object_or_404(
        SOItem.objects.select_related('order'), pk=request.POST.get('item'),
    )
    order = item.order

    existing = item.current_job_card
    if existing:
        return redirect('production:job_card', jc_no=existing.number)

    # Re-check the gate: the button was rendered from a queryset, but the POST
    # could have been hand-rolled, or the state could have moved since.
    if not (item.design_approved and item.material_issued and order.is_ready_to_produce):
        return redirect('production:dashboard')

    card = JobCard.create_for([item], request.user, priority=order.priority)
    order.refresh_status()

    log(request.user, 'JobCard', order.number, f'{card.number} released to production',
        detail=f'Item #{item.position}', to_value=order.status)
    notify(order.assigned_designer, f'{card.number} released for {order.number}',
           object_ref=order.number)
    return redirect('production:job_card', jc_no=card.number)


# ------------------------------------------------------------------ floor
def _get_card(jc_no):
    return get_object_or_404(
        JobCard.objects
        .select_related('floor_manager')
        .prefetch_related(
            'items__so_item__order__customer',
            'items__so_item__quotation_item',
        ),
        number__iexact=jc_no,
    )


@role_required('is_floor_manager')
def job_card(request, jc_no):
    """One real job card. The station list is whatever build_stations() laid out."""
    card = _get_card(jc_no)
    card_items = card.so_items
    # The floor sheet still leads on one item; a multi-item card lists the
    # rest underneath. `items` is the full set, `item` is the lead line.
    item = card_items[0] if card_items else None
    order = item.order if item else card.primary_order

    stations = list(
        card.stations.select_related('station', 'operator').order_by('sequence')
    )
    confirmed = card.stations_confirmed_at is not None
    current = card.current_station if confirmed else None

    reservation = item.reservations.filter(
        released_at__isnull=True,
    ).select_related('sheet', 'sheet__size').first() if item else None

    # Operator roster, grouped by the station master's group (§3.8).
    roster = {}
    for row in stations:
        group = row.station.group or 'Ungrouped'
        who = roster.setdefault(group, set())
        if row.operator:
            who.add(row.operator.short_name)
    roster = [
        {'group': g, 'operators': ', '.join(sorted(names)) or '—'}
        for g, names in sorted(roster.items())
    ]

    if not card.is_started:
        pill_class, pill_text = 'is-idle', 'Not started'
    elif not confirmed:
        pill_class, pill_text = 'is-selecting', 'Selecting stations'
    elif card.is_complete:
        pill_class = 'is-complete'
        pill_text = f'Completed — {card.done_count} of {card.applicable_count} stations'
    else:
        name = current.station.name if current else ''
        pill_class = ''
        pill_text = f'Station {card.done_count + 1} of {card.applicable_count} — {name}'

    return render(request, 'production/job_card.html', {
        'jc_no': card.number,
        'card': card,
        'item': item,
        'card_items': card_items,
        'order': order,
        'stations': stations,
        'master_count': len(stations),
        'confirmed': confirmed,
        'current_station': current,
        'reservation': reservation,
        'sheet': reservation.sheet if reservation else None,
        'drawing': item.current_drawing if item else None,
        'roster': roster,
        'pill_class': pill_class,
        'pill_text': pill_text,
    })


@role_required('is_floor_manager')
@require_POST
def start_work(request, jc_no):
    """Captures the start time. Station selection stays locked until this runs."""
    card = _get_card(jc_no)
    if not card.is_started:
        card.start_work(request.user)
        log(request.user, 'JobCard', card.order_ref, f'{card.number} work started')
    return redirect('production:job_card', jc_no=card.number)


@role_required('is_floor_manager')
@require_POST
def confirm_stations(request, jc_no):
    """Blocked before Start Work, and only ever confirmed once."""
    card = _get_card(jc_no)
    if not card.is_started or card.stations_confirmed_at:
        return redirect('production:job_card', jc_no=card.number)

    form = StationSelectionForm(request.POST, job_card=card)
    if form.is_valid():
        selected = list(form.cleaned_data['stations'])
        if selected:
            card.confirm_stations([row.pk for row in selected])
            log(request.user, 'JobCard', card.order_ref,
                f'{card.number} stations confirmed',
                detail=f'{len(selected)} applicable of {card.stations.count()}')
    return redirect('production:job_card', jc_no=card.number)


@role_required('is_floor_manager')
@require_POST
def complete_station(request, jc_no):
    """Blocked until the station list is confirmed; duration is computed, not typed."""
    card = _get_card(jc_no)
    if not card.stations_confirmed_at:
        return redirect('production:job_card', jc_no=card.number)

    row = get_object_or_404(
        JobCardStation.objects.select_related('station'),
        pk=request.POST.get('station'), job_card=card,
    )
    if row.status in {StationStatus.PENDING, StationStatus.IN_PROGRESS}:
        card.complete_station(row, request.user)
        log(request.user, 'JobCardStation', card.order_ref,
            f'{row.station.name} completed',
            detail=f'{card.number} · {row.duration_display}')
    return redirect('production:job_card', jc_no=card.number)
