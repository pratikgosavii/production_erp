"""Production planning screens and the shop-floor job card (§3.8, §4, §10.1·3).

The gate this module defends is the one from §4: a job reaches the floor only
when the drawing is approved AND the material has been *issued* by Stores.
A reservation is not enough, so readiness is read off SOItem.is_ready, which
asks for a live reservation carrying an issued_at stamp.

The Production Manager works top-down across three screens:

  A. `dashboard`      — every live Sales Order, newest first, with its PO, its
                        value and how many of its item codes are ready. This is
                        the landing page: an order is the unit he thinks in.
  B. `order_detail`   — ONE order's item codes. Ready ones selectable, blocked
                        ones greyed with their own reason, carded ones linked
                        to their JC. Selection is a courtesy; `create_job_card`
                        re-checks every gate server-side.
  C. `priority_board` — the same orders as drag-and-drop cards in four priority
                        columns (§4 fixed priority at numeric 1-4). Dropping a
                        card writes `priority`; reordering inside a column
                        writes `queue_rank`.

The earlier flat item queue mixed every item code of every order into one list,
which hid the shape of an order and buried the PO. It has moved wholesale into
Screen B, scoped to a single order.

The job card enforces its own ordering server-side — Start Work, then confirm
the station list, then mark stations complete. Disabled buttons are a courtesy;
the checks below are the rule.
"""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import SalesOrder, SOItem
from masters.models import ProcessStation
from core.constants import (
    Disposition, OrderStatus, OrderType, Priority, RejectionReason, StationStatus,
)
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

# Screen A / C list the same population: everything except the terminal state.
# ON_HOLD stays visible (greyed, with its reason) because the planner has to
# know an order exists before he can chase the hold.
BOARD_EXCLUDED_STATUSES = [OrderStatus.CANCELLED]


def _due_bucket(days):
    if days is None:
        return 'ok'
    if days <= DUE_CRITICAL_DAYS:
        return 'crit'
    if days <= DUE_SOON_DAYS:
        return 'soon'
    return 'ok'


def _live_orders():
    """Every Sales Order the Production Manager can still act on.

    Prefetches everything `is_ready` / `blocked_reason` reach for, so a 40-order
    board does not turn into several hundred queries.
    """
    return (
        SalesOrder.objects
        .exclude(status__in=BOARD_EXCLUDED_STATUSES)
        .select_related('customer', 'quotation')
        .prefetch_related(
            'queries',
            'items__quotation_item__item_code',
            'items__quotation_item__material',
            'items__quotation_item__thickness',
            'items__quotation_item__finish',
            'items__reservations',
            'items__queries',
            'items__drawing_revisions',
            'items__job_card_items__job_card',
        )
    )


def _po_chip(order):
    """The PO as the planner asks for it: a filename that opens the file.

    Falls back to the bare PO number when Accounts recorded one without
    attaching a scan, and to nothing at all when neither exists.
    """
    quotation = order.quotation
    if quotation.po_file:
        name = quotation.po_file.name.replace('\\', '/').rsplit('/', 1)[-1]
        return {'name': name, 'url': quotation.po_file.url}
    if quotation.po_number:
        return {'name': quotation.po_number, 'url': ''}
    return {'name': '', 'url': ''}


def _item_state(item):
    """('carded' | 'ready' | 'blocked', job card or None, reason).

    An item on a card is neither ready nor blocked — it has left the queue.

    "Ready" here means ready to PLAN, not ready to cut. An item whose drawing
    is approved but whose material Stores has not issued yet is selectable:
    the PM cards it and sets its priority, and the floor is what waits for the
    trolley. `awaiting_material` carries that caveat to the template.
    """
    card = item.current_job_card
    if card is not None:
        return 'carded', card, ''
    if item.is_plannable:
        return 'ready', None, ''
    return 'blocked', None, (item.blocked_reason_for_planning or 'Not ready')


def _order_counts(order):
    """How the item codes of one order split three ways."""
    ready = carded = blocked = 0
    for item in order.items.all():
        state, _card, _reason = _item_state(item)
        if state == 'carded':
            carded += 1
        elif state == 'ready':
            ready += 1
        else:
            blocked += 1
    total = ready + carded + blocked
    return {
        'total': total,
        'ready': ready,
        'carded': carded,
        'blocked': blocked,
        'ready_pct': round(ready / total * 100) if total else 0,
        'carded_pct': round(carded / total * 100) if total else 0,
        'blocked_pct': round(blocked / total * 100) if total else 0,
    }


def _order_card(order, today):
    """The shared shape Screens A and C both draw an order from."""
    days = (order.expected_delivery - today).days if order.expected_delivery else None
    counts = _order_counts(order)
    return {
        'order': order,
        'counts': counts,
        'po': _po_chip(order),
        'days': days,
        'due_class': _due_bucket(days),
        'on_hold': order.status == OrderStatus.ON_HOLD,
        'is_repeat': order.order_type == OrderType.REPEAT,
        'detail_url': reverse('production:order_detail', args=[order.number]),
    }


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


# --------------------------------------------------- Screen A: sales orders
# Filter chips. Each is a plain querystring link, so the whole screen stays
# bookmarkable and works with JavaScript switched off.
ORDER_FILTERS = [
    ('all', 'All live orders'),
    ('ready', 'Has ready items'),
    ('blocked', 'Fully blocked'),
    ('hold', 'On hold'),
    ('repeat', 'Repeat orders'),
]


def _matches_filter(row, key):
    if key == 'ready':
        return row['counts']['ready'] > 0
    if key == 'blocked':
        return row['counts']['total'] > 0 and row['counts']['blocked'] == row['counts']['total']
    if key == 'hold':
        return row['on_hold']
    if key == 'repeat':
        return row['is_repeat']
    return True


@role_required('is_production_manager')
def dashboard(request):
    """Screen A — live Sales Orders, newest first. The planning landing page.

    Newest first is the whole point: the flat item queue sorted by priority and
    buried a just-booked order at the bottom of a 37-row list.
    """
    today = timezone.localdate()

    active = request.GET.get('f', 'all')
    if active not in dict(ORDER_FILTERS):
        active = 'all'
    query = (request.GET.get('q') or '').strip()

    orders = _live_orders()
    if query:
        orders = orders.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(quotation__number__icontains=query)
        )
    # Newest first. booked_on is nullable, so `-number` is the tie-break that
    # keeps an unbooked-date order from floating to an arbitrary place.
    orders = orders.order_by('-booked_on', '-number')

    rows = [_order_card(order, today) for order in orders]

    # The strip counts the whole live population, not the filtered slice —
    # otherwise clicking a chip would silently rewrite the headline numbers.
    summary = {
        'orders': len(rows),
        'with_ready': sum(1 for r in rows if r['counts']['ready']),
        'items_ready': sum(r['counts']['ready'] for r in rows),
        'items_blocked': sum(r['counts']['blocked'] for r in rows),
    }

    chips = []
    for key, label in ORDER_FILTERS:
        params = []
        if key != 'all':
            params.append(f'f={key}')
        if query:
            params.append(f'q={query}')
        chips.append({
            'key': key,
            'label': label,
            'count': sum(1 for r in rows if _matches_filter(r, key)),
            'url': '?' + '&'.join(params) if params else '?',
            'active': key == active,
        })

    rows = [r for r in rows if _matches_filter(r, active)]

    return render(request, 'production/dashboard.html', {
        'rows': rows,
        'summary': summary,
        'chips': chips,
        'active_filter': active,
        'q': query,
        'nav': 'orders',
    })


# ---------------------------------------------------- Screen B: one order
@role_required('is_production_manager')
def order_detail(request, so_no):
    """Screen B — the item codes of ONE order; the old flat queue, scoped.

    Selection is deliberately confined to this order: bundling across orders is
    what made the old screen unreadable, and `create_job_card` still accepts a
    cross-order set if one is ever posted.
    """
    # Not _live_orders(): a cancelled order still has to open, or the job cards
    # already raised against it would become unreachable from here.
    order = get_object_or_404(
        SalesOrder.objects
        .select_related('customer', 'quotation')
        .prefetch_related(
            'queries',
            'items__quotation_item__item_code',
            'items__quotation_item__material',
            'items__quotation_item__thickness',
            'items__quotation_item__finish',
            'items__reservations',
            'items__queries',
            'items__drawing_revisions',
            'items__job_card_items__job_card',
        ),
        number__iexact=so_no,
    )

    today = timezone.localdate()
    days = (order.expected_delivery - today).days if order.expected_delivery else None

    rows = []
    for item in order.items.all():
        state, card, reason = _item_state(item)
        rows.append({
            'item': item,
            'code': _item_code_text(item),
            'spec': _spec_text(item),
            'qty': item.quantity,
            'value': item.quotation_item.line_amount,
            'state': state,
            'is_ready': state == 'ready',
            # Selectable, but the floor will hold it until Stores issues.
            # Shown as a caveat on the row, never as a block.
            'awaiting_material': state == 'ready' and item.awaiting_material,
            # blocked_reason is the item's own words; never re-worded here.
            'reason': reason,
            'card': card,
        })

    cards = (
        JobCard.objects
        .filter(items__so_item__order=order)
        .distinct()
        .prefetch_related(
            'items__so_item__quotation_item__item_code', 'stations',
        )
        .order_by('-created_at')
    )
    # What the edit dialog may offer for each card: the items already on it,
    # plus this order's items that are plannable and not on another card.
    # Anything else would fail edit_job_card's re-check anyway.
    spare = [r['item'] for r in rows if r['state'] == 'ready']
    order_cards = []
    for card in cards:
        on_card = [link.so_item for link in card.items.all()]
        on_pks = {i.pk for i in on_card}
        order_cards.append({
            'card': card,
            'state': _job_card_state(card),
            'codes': [_item_code_text(link.so_item) for link in card.items.all()],
            'choices': [
                {'pk': i.pk, 'code': _item_code_text(i), 'spec': _spec_text(i),
                 'qty': i.quantity, 'on_card': i.pk in on_pks}
                for i in on_card + [s for s in spare if s.pk not in on_pks]
            ],
        })

    return render(request, 'production/order_detail.html', {
        'order': order,
        'rows': rows,
        # The steps a new card will be laid out from, so the instruction for
        # each one can be written before the card exists.
        'station_master': ProcessStation.objects.filter(is_active=True)
                                        .order_by('sequence', 'name'),
        # Keyed by JC number so one edit dialog can serve every tile.
        'jc_choices': {c['card'].number: c['choices'] for c in order_cards},
        'counts': _order_counts(order),
        'po': _po_chip(order),
        'days': days,
        'due_class': _due_bucket(days),
        'on_hold': order.status == OrderStatus.ON_HOLD,
        'order_cards': order_cards,
        'nav': 'orders',
        'nav_order': order.number,
    })


# ------------------------------------------------- Screen C: priority board
@role_required('is_production_manager')
def priority_board(request):
    """Screen C — one draggable card per live order, in four priority columns."""
    today = timezone.localdate()

    orders = _live_orders().order_by('queue_rank', '-booked_on', '-number')
    rows = [_order_card(order, today) for order in orders]

    columns = [
        {'value': value, 'label': label, 'cards': [
            r for r in rows if r['order'].priority == value
        ]}
        for value, label in Priority.choices
    ]

    return render(request, 'production/priority_board.html', {
        'columns': columns,
        'priorities': Priority.choices,
        'total': len(rows),
        'save_url': reverse('production:save_priority'),
        'nav': 'priority',
    })


def _safe_next(request, fallback):
    """Only ever redirect back to somewhere on this site."""
    target = request.POST.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return target
    return fallback


def _apply_priority(user, order, priority, queue_rank=None):
    """Write one card's arrangement, logging only a genuine priority change."""
    fields = []
    was = order.priority
    if priority is not None and priority != order.priority:
        order.priority = priority
        fields.append('priority')
    if queue_rank is not None and queue_rank != order.queue_rank:
        order.queue_rank = queue_rank
        fields.append('queue_rank')
    if not fields:
        return False
    order.save(update_fields=fields + ['updated_at'])
    if 'priority' in fields:
        log(user, 'SalesOrder', order.number, 'Priority changed',
            from_value=was, to_value=order.priority)
    return True


@role_required('is_production_manager')
@require_POST
def save_priority(request):
    """Persist the board arrangement — priority AND position within a column.

    Two callers, one place the write happens:
      * the board's fetch posts JSON [{order_id, priority, queue_rank}, ...]
        and gets JSON back, so the page can toast without reloading;
      * the no-JavaScript <select> (and the older per-order/per-item forms)
        post a normal form and get a redirect.

    Everything lands in ONE transaction: a half-applied board is worse than a
    rejected one, because the screen would then disagree with the database.
    """
    is_json = 'application/json' in (request.content_type or '')

    if is_json:
        try:
            payload = json.loads(request.body.decode('utf-8') or '[]')
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'ok': False, 'error': 'Malformed request body.'}, status=400)
        if isinstance(payload, dict):
            payload = payload.get('orders') or []
        if not isinstance(payload, list):
            return JsonResponse({'ok': False, 'error': 'Expected a list of cards.'}, status=400)

        wanted = {}
        for entry in payload:
            if not isinstance(entry, dict):
                return JsonResponse({'ok': False, 'error': 'Expected a list of cards.'}, status=400)
            try:
                pk = int(entry.get('order_id'))
                priority = int(entry.get('priority'))
                rank = int(entry.get('queue_rank', 0))
            except (TypeError, ValueError):
                return JsonResponse({'ok': False, 'error': 'Unreadable card in the arrangement.'}, status=400)
            if priority not in Priority.values:
                return JsonResponse({'ok': False, 'error': f'Priority {priority} is not 1-4.'}, status=400)
            wanted[pk] = (priority, rank)

        found = {
            order.pk: order for order in
            SalesOrder.objects.filter(pk__in=wanted).exclude(status__in=BOARD_EXCLUDED_STATUSES)
        }
        missing = set(wanted) - set(found)
        if missing:
            return JsonResponse(
                {'ok': False, 'error': 'Some orders on the board no longer exist.'}, status=400,
            )

        changed = 0
        with transaction.atomic():
            for pk, (priority, rank) in wanted.items():
                if _apply_priority(request.user, found[pk], priority, rank):
                    changed += 1
        return JsonResponse({'ok': True, 'saved': len(wanted), 'changed': changed})

    # ---- plain form post (no-JavaScript fallback, and the older callers) ----
    fallback = reverse('production:dashboard')
    if request.POST.get('order'):
        order = get_object_or_404(SalesOrder, pk=request.POST.get('order'))
    elif request.POST.get('item'):
        order = get_object_or_404(SOItem, pk=request.POST.get('item')).order
    else:
        messages.error(request, 'No sales order was named, so nothing was changed.')
        return redirect(_safe_next(request, fallback))

    form = PriorityForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'That priority is not one of 1-4. Nothing was changed.')
        return redirect(_safe_next(request, fallback))

    with transaction.atomic():
        _apply_priority(request.user, order, form.cleaned_data['priority'])
    messages.success(request, f'{order.number} set to priority {order.priority}.')
    return redirect(_safe_next(request, fallback))


@role_required('is_production_manager')
@require_POST
def create_job_card(request):
    """Mint ONE job card covering the ticked item codes (§3.8).

    Everything the template disabled is re-checked here: a hand-rolled POST
    must not be able to put a blocked item, or an item already on a card, onto
    the floor. Any rejection changes nothing at all, and lands back on the
    order the selection was made from.
    """
    raw_ids = request.POST.getlist('items')
    so_no = (request.POST.get('so_no') or '').strip()

    def refuse(text):
        messages.error(request, text)
        if so_no:
            return redirect('production:order_detail', so_no=so_no)
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
        elif not item.is_plannable:
            blocked.append(
                f'{_item_code_text(item)} — '
                f'{item.blocked_reason_for_planning or "not ready"}'
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

    # Per-stage instructions typed on the way in, posted as desc_<station pk>.
    # Written onto this card's rows, so they describe THIS job — the etching
    # note for 0.5mm SS is not the note for 3mm mild steel.
    descriptions = {}
    for key, value in request.POST.items():
        if key.startswith('desc_') and (value or '').strip():
            try:
                descriptions[int(key[5:])] = value
            except ValueError:
                continue

    try:
        with transaction.atomic():
            card = JobCard.create_for(
                found, request.user,
                is_urgent_split=is_urgent, urgent_reason=reason,
                descriptions=descriptions,
            )
            orders = list(card.orders)
            for order in orders:
                order.refresh_status()
    except (ValidationError, ValueError) as exc:
        return refuse(f'Job card not created: {exc}')

    codes = ', '.join(sorted(_item_code_text(i) for i in found))
    if descriptions:
        codes += f' · {len(descriptions)} stage instruction(s) written'
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
        f'{"" if len(found) == 1 else "s"} ({codes}). It is now with the floor.',
    )
    # Back to the order, NOT to the job card: that screen belongs to the Floor
    # Manager, so sending the Production Manager there minted the card and then
    # threw 403 on the redirect.
    if so_no:
        return redirect('production:order_detail', so_no=so_no)
    return redirect('production:dashboard')


@role_required('is_production_manager')
@require_POST
def edit_job_card(request, jc_no):
    """Change a card's membership, priority and urgent flag — before Start Work.

    Editable membership is scoped to the order the card belongs to: an item is
    either already on this card, or plannable and not on another one.
    """
    card = get_object_or_404(JobCard, number__iexact=jc_no)
    so_no = request.POST.get('so_no') or ''

    def back(msg=None, error=False):
        if msg:
            (messages.error if error else messages.success)(request, msg)
        if so_no:
            return redirect('production:order_detail', so_no=so_no)
        return redirect('production:dashboard')

    if not card.can_edit:
        return back(
            f'{card.number} has already started on the floor, so its item codes '
            f'can no longer be changed. It can still be deleted.', error=True)

    on_card = set(card.items.values_list('so_item_id', flat=True))
    try:
        wanted = {int(pk) for pk in request.POST.getlist('items')}
    except (TypeError, ValueError):
        return back('That selection was not understood. Nothing was changed.', error=True)

    if not wanted:
        return back('A job card must keep at least one item code. Delete it '
                    'instead if it is not wanted.', error=True)

    found = list(
        SOItem.objects.filter(pk__in=wanted)
        .select_related('order', 'quotation_item__item_code')
    )
    if len(found) != len(wanted):
        return back('Some selected item codes no longer exist. Nothing was changed.',
                    error=True)

    # Anything being ADDED has to pass the same gate create_job_card applies.
    refused = []
    for item in found:
        if item.pk in on_card:
            continue
        if item.job_card_items.exists():
            refused.append(f'{_item_code_text(item)} (on {item.current_job_card})')
        elif item.order.status in DEAD_ORDER_STATUSES:
            refused.append(f'{_item_code_text(item)} — {item.order.get_status_display()}')
        elif not item.is_plannable:
            refused.append(
                f'{_item_code_text(item)} — '
                f'{item.blocked_reason_for_planning or "not ready"}')
    if refused:
        return back('Nothing was changed: ' + '; '.join(refused), error=True)

    priority = request.POST.get('priority')
    urgent = bool(request.POST.get('is_urgent_split'))
    reason = (request.POST.get('urgent_reason') or '').strip()
    if urgent and not reason:
        return back('An urgent split must say why it jumped the queue. '
                    'Nothing was changed.', error=True)

    try:
        with transaction.atomic():
            card.set_items(found, request.user)
            if priority and str(priority).isdigit():
                card.priority = int(priority)
            card.is_urgent_split = urgent
            card.urgent_reason = reason if urgent else ''
            card.save(update_fields=[
                'priority', 'is_urgent_split', 'urgent_reason', 'updated_at'])
    except (ValidationError, ValueError) as exc:
        return back(f'{card.number} was not changed — {exc}', error=True)

    codes = ', '.join(_item_code_text(i) for i in card.so_items)
    log(request.user, 'JobCard', card.order_ref, f'{card.number} edited',
        detail=f'{card.item_count} item code(s): {codes} · P{card.priority}')
    return back(f'{card.number} updated — {card.item_count} item code(s): {codes}.')


@role_required('is_production_manager')
@require_POST
def delete_job_card(request, jc_no):
    """Remove a job card. Allowed before AND after Start Work.

    After Start Work this destroys recorded station timings, which is why the
    audit entry is written BEFORE the delete — the stations cascade away with
    the card, so that entry is all that survives.
    """
    card = get_object_or_404(JobCard, number__iexact=jc_no)
    so_no = request.POST.get('so_no') or ''
    codes = ', '.join(_item_code_text(i) for i in card.so_items)
    order_ref = card.order_ref
    number = card.number

    detail = f'{card.item_count} item code(s): {codes}'
    if card.is_started:
        detail += (f' · started {card.started_at:%d %b %Y %H:%M}'
                   f' · {card.done_count} of {card.applicable_count} stations completed')
    log(request.user, 'JobCard', order_ref, f'{number} deleted', detail=detail)

    card.delete_card(request.user)
    messages.success(
        request,
        f'{number} deleted. Its item codes are selectable again: {codes}.',
    )
    if so_no:
        return redirect('production:order_detail', so_no=so_no)
    return redirect('production:dashboard')


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
        messages.info(request, f'That item is already on {existing.number}.')
        return redirect('production:order_detail', so_no=order.number)

    # Re-check the gate: the button was rendered from a queryset, but the POST
    # could have been hand-rolled, or the state could have moved since. This is
    # the PLANNING gate — material issue is checked on the floor, at Start Work.
    if not item.is_plannable:
        return redirect('production:order_detail', so_no=order.number)

    card = JobCard.create_for([item], request.user, priority=order.priority)
    order.refresh_status()

    log(request.user, 'JobCard', order.number, f'{card.number} released to production',
        detail=f'Item #{item.position}', to_value=order.status)
    notify(order.assigned_designer, f'{card.number} released for {order.number}',
           object_ref=order.number)
    messages.success(request, f'{card.number} created. It is now with the floor.')
    # The job card screen is the Floor Manager's — a Production Manager sent
    # there would meet a 403 straight after a successful release.
    return redirect('production:order_detail', so_no=order.number)


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


# The floor's own landing screen. Before this existed the Floor Manager's
# landing page was the PM dashboard, which is is_production_manager-only — so
# signing in as floor staff went straight to "No access", and a job card could
# only be reached by typing its URL.
FLOOR_FILTERS = [
    ('open', 'Open work'),
    ('waiting', 'Waiting on material'),
    ('running', 'In production'),
    ('done', 'Completed'),
    ('all', 'All cards'),
]


@role_required('is_floor_manager', 'is_production_manager')
def floor_queue(request):
    """Every job card released to the floor, newest first.

    Newest-first matches every other list in the ERP. Note this means priority
    no longer drives the order — a P1 released last week now sits below a P4
    released today, so the priority chip is the thing to read, not position.
    Cards waiting on Stores stay listed rather than hidden: the Floor Manager
    needs to see what is coming and why it cannot start.
    """
    cards = (
        JobCard.objects
        .select_related('floor_manager', 'released_by')
        .prefetch_related(
            'stations',
            'items__so_item__order__customer',
            'items__so_item__quotation_item__item_code',
            'items__so_item__reservations',
        )
        .order_by('-released_at', '-number')
    )

    rows = []
    for card in cards:
        waiting = card.items_awaiting_material
        if card.is_complete:
            key, label, css = 'done', 'Completed', 'done'
        elif waiting:
            key, label, css = 'waiting', 'Waiting on material', 'waiting'
        elif card.stations_confirmed_at:
            station = card.current_station
            key, css = 'running', 'running'
            label = (f'Station {card.done_count + 1} of {card.applicable_count}'
                     f' — {station.station.name}' if station else 'In production')
        elif card.is_started:
            key, label, css = 'running', 'Selecting stations', 'running'
        else:
            key, label, css = 'ready', 'Ready to start', 'ready'

        rows.append({
            'card': card,
            'state_key': key,
            'state_label': label,
            'state_css': css,
            'waiting_on': ', '.join(_item_code_text(i) for i in waiting),
            'codes': ', '.join(_item_code_text(i) for i in card.so_items) or '—',
            'order_ref': card.order_ref,
            'customer': next(
                (i.order.customer.name for i in card.so_items if i.order.customer),
                '',
            ),
            'item_count': card.item_count,
            'can_start': card.can_start and not card.is_started,
            # released_at is stamped by create_for; created_at is the safety
            # net for any card written outside that path (admin, fixtures).
            'created': card.released_at or card.created_at,
            'started': card.started_at,
            'completed': card.completed_at,
        })

    active = request.GET.get('filter', 'open')
    if active not in dict(FLOOR_FILTERS):
        active = 'open'

    def keep(row, key):
        if key == 'all':
            return True
        if key == 'open':                      # anything the floor still owes work on
            return row['state_key'] != 'done'
        if key == 'waiting':
            return row['state_key'] == 'waiting'
        if key == 'running':
            return row['state_key'] == 'running'
        return row['state_key'] == 'done'

    chips = [
        {'key': key, 'label': label, 'count': sum(1 for r in rows if keep(r, key)),
         'is_active': key == active}
        for key, label in FLOOR_FILTERS
    ]
    visible = [r for r in rows if keep(r, active)]

    return render(request, 'production/floor_queue.html', {
        'rows': visible,
        'chips': chips,
        'active_filter': active,
        'summary': {
            'ready': sum(1 for r in rows if r['state_key'] == 'ready'),
            'waiting': sum(1 for r in rows if r['state_key'] == 'waiting'),
            'running': sum(1 for r in rows if r['state_key'] == 'running'),
        },
        'can_run': bool(request.user.is_floor_manager or request.user.is_superuser),
        'can_open_order': bool(
            request.user.is_production_manager or request.user.is_superuser),
        'nav': 'floor',
    })


@role_required('is_floor_manager', 'is_production_manager')
def job_card(request, jc_no):
    """One real job card. The station list is whatever build_stations() laid out.

    The Production Manager can READ this — he released the card and needs to
    see how it is going — but only the Floor Manager may start work, confirm
    stations or mark one complete. Those POSTs stay floor-only.
    """
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

    # The right rail shows only the LEAD item's sheet, which is wrong the
    # moment a card covers more than one item code. This is the full material
    # trail — every sheet on the card, when Design reserved it and when Stores
    # actually handed it over.
    # Same story as the sheet: the rail showed only the lead item's drawing,
    # so on a multi-item card the other drawings were simply absent — and the
    # filename was plain text, so the floor could not open any of them.
    drawings = []
    for so_item in card_items:
        rev = so_item.current_drawing
        drawings.append({
            'code': _item_code_text(so_item),
            'revision': rev,
            'name': (rev.file.name.rsplit('/', 1)[-1]
                     if rev and rev.file else ''),
            'url': rev.file.url if rev and rev.file else '',
            'label': rev.label if rev else '',
            'approved': so_item.design_approved,
        })

    materials = []
    for so_item in card_items:
        res = (
            so_item.reservations
            .filter(released_at__isnull=True)
            .select_related('sheet', 'sheet__size', 'reserved_by', 'issued_by')
            .first()
        )
        qi = so_item.quotation_item
        row = {
            'code': _item_code_text(so_item),
            'spec': _spec_text(so_item),
            'qty': qi.quantity,
        }
        if res is None:
            row.update({'uid': '', 'state': 'Not reserved', 'css': 'none'})
        elif res.issued_at:
            row.update({
                'uid': res.sheet.uid,
                'is_offcut': res.sheet.is_offcut,
                'size': res.sheet.size_display,
                'rack': res.sheet.rack.name if res.sheet.rack_id else '',
                'state': 'Issued', 'css': 'issued',
                'reserved_at': res.reserved_at, 'reserved_by': res.reserved_by,
                'issued_at': res.issued_at, 'issued_by': res.issued_by,
            })
        else:
            row.update({
                'uid': res.sheet.uid,
                'is_offcut': res.sheet.is_offcut,
                'size': res.sheet.size_display,
                'rack': res.sheet.rack.name if res.sheet.rack_id else '',
                'state': 'Reserved — awaiting issue', 'css': 'reserved',
                'reserved_at': res.reserved_at, 'reserved_by': res.reserved_by,
                'issued_at': None, 'issued_by': None,
            })
        materials.append(row)

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
        'materials': materials,
        'drawing': item.current_drawing if item else None,
        'drawings': drawings,
        'roster': roster,
        'pill_class': pill_class,
        'pill_text': pill_text,
        'nav': 'floor',
        'nav_card': card.number,
        'rejection_reasons': RejectionReason.choices,
        'dispositions': Disposition.choices,
        # Only the floor runs the card; the Production Manager is here to watch.
        'can_run': bool(request.user.is_floor_manager or request.user.is_superuser),
        # ...but the instructions are his to write, before the station runs.
        'can_edit_notes': bool(
            request.user.is_production_manager or request.user.is_superuser),
        # Drives the prompt: an empty card should say so loudly, a documented
        # one should get out of the way.
        'notes_written': sum(
            1 for s in stations
            if s.description.strip() and s.status != StationStatus.SKIPPED),
        'notes_total': sum(
            1 for s in stations if s.status != StationStatus.SKIPPED),
        # The order link is is_production_manager-only, so the floor must not
        # be offered it — they would land on "No access".
        'can_open_order': bool(
            request.user.is_production_manager or request.user.is_superuser),
        'nav_order': order.number if order else '',
    })


@role_required('is_floor_manager')
@require_POST
def start_work(request, jc_no):
    """Captures the start time. Station selection stays locked until this runs."""
    card = _get_card(jc_no)
    if not card.can_start:
        # The material gate, enforced on the floor rather than in planning.
        waiting = ', '.join(
            str(i.quotation_item.item_code or i.quotation_item.description or i.pk)
            for i in card.items_awaiting_material
        )
        messages.error(
            request,
            f'{card.number} cannot start — Stores has not issued material for: '
            f'{waiting}. The card stays on the floor queue until they do.',
        )
    elif not card.is_started:
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


@role_required('is_production_manager')
@require_POST
def save_station_notes(request, jc_no):
    """Add or change the stage instructions on a card that already exists.

    Only stations that have NOT been completed can be edited. Rewriting the
    instruction on a finished station would change what the operator was told
    after he acted on it, which is exactly the record an audit is looking at.
    """
    card = _get_card(jc_no)
    changed, locked = [], []

    with transaction.atomic():
        for row in card.stations.select_related('station'):
            key = f'desc_{row.pk}'
            if key not in request.POST:
                continue
            text = (request.POST.get(key) or '').strip()
            if text == row.description:
                continue
            if row.status == StationStatus.DONE:
                locked.append(row.station.name)
                continue
            row.description = text
            row.save(update_fields=['description', 'updated_at'])
            changed.append(row.station.name)

    if changed:
        log(request.user, 'JobCard', card.order_ref,
            f'{card.number} stage instructions updated',
            detail=', '.join(changed))
        messages.success(
            request,
            f'Instructions updated on {card.number}: {", ".join(changed)}.')
    if locked:
        messages.error(
            request,
            f'Left unchanged because the station is already complete: '
            f'{", ".join(locked)}.')
    if not changed and not locked:
        messages.info(request, 'Nothing was changed.')
    return redirect('production:job_card', jc_no=card.number)


@role_required('is_floor_manager')
@require_POST
def start_station(request, jc_no):
    """Operator picks up one station — this is what starts its clock.

    Only one station runs at a time: the card is a physical batch moving down
    the line, so a second concurrent start would make both durations lies.
    """
    card = _get_card(jc_no)
    if not card.stations_confirmed_at:
        return redirect('production:job_card', jc_no=card.number)

    row = get_object_or_404(
        JobCardStation.objects.select_related('station'),
        pk=request.POST.get('station'), job_card=card,
    )
    running = card.stations.filter(status=StationStatus.IN_PROGRESS).exclude(pk=row.pk).first()
    if running is not None:
        messages.error(
            request,
            f'{running.station.name} is still running. Complete it before '
            f'starting {row.station.name}.')
    elif row.status == StationStatus.PENDING:
        row.start(request.user)
        log(request.user, 'JobCardStation', card.order_ref,
            f'{row.station.name} started', detail=card.number)
    return redirect('production:job_card', jc_no=card.number)


def _int_or(value, fallback=None):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


@role_required('is_floor_manager')
@require_POST
def complete_station(request, jc_no):
    """Close a station and record its production log.

    Duration is still computed, never typed. The quantities ARE typed — the
    operator is the only one who knows how many survived — but they have to
    balance: in = good + rejected, or the yield figures downstream are fiction.
    """
    card = _get_card(jc_no)
    if not card.stations_confirmed_at:
        return redirect('production:job_card', jc_no=card.number)

    row = get_object_or_404(
        JobCardStation.objects.select_related('station'),
        pk=request.POST.get('station'), job_card=card,
    )
    def refuse(msg):
        messages.error(request, f'{row.station.name} was not closed — {msg}')
        return redirect('production:job_card', jc_no=card.number)

    if row.status == StationStatus.DONE or row.status == StationStatus.SKIPPED:
        return redirect('production:job_card', jc_no=card.number)
    if row.status != StationStatus.IN_PROGRESS:
        # Closing a station that was never started would fall back to the old
        # inferred duration — the gap since the previous station finished,
        # which counts overnight and weekends as run time. Press Start.
        return refuse('press Start first, or the recorded time is the gap '
                      'since the last station finished, not the work.')

    qty_in = _int_or(request.POST.get('qty_in'), row.expected_qty_in)
    qty_out = _int_or(request.POST.get('qty_out'))
    qty_rej = _int_or(request.POST.get('qty_rejected'), 0)
    reason = (request.POST.get('rejection_reason') or '').strip()
    disposition = (request.POST.get('disposition') or '').strip()

    if qty_out is None:
        return refuse('enter how many good pieces came out.')
    if qty_out + qty_rej != qty_in:
        return refuse(
            f'the numbers do not balance: {qty_out} good + {qty_rej} rejected '
            f'= {qty_out + qty_rej}, but {qty_in} went in.')
    if qty_rej and not reason:
        return refuse('a rejection needs a reason — that is the whole point of '
                      'recording it.')
    if qty_rej and not disposition:
        return refuse('say what happens to the rejected pieces: scrap, rework '
                      'or use as is.')

    card.complete_station(
        row, request.user, qty_in=qty_in, qty_out=qty_out,
        qty_rejected=qty_rej, rejection_reason=reason, disposition=disposition,
    )
    detail = f'{card.number} · {row.duration_display} · {qty_out} good'
    if qty_rej:
        detail += (f' · {qty_rej} rejected ({row.get_rejection_reason_display()}'
                   f' → {row.get_disposition_display()})')
    log(request.user, 'JobCardStation', card.order_ref,
        f'{row.station.name} completed', detail=detail)
    return redirect('production:job_card', jc_no=card.number)
