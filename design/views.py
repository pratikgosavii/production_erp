"""Design Queue and Design Job Detail (§3.6, §7.1, §10.2).

Three rules shape this module:

* One drawing per ITEM CODE, uploaded by DESIGN (§10.2·1). The PO is common to
  the order and carries no drawing, so an order lands here with nothing drawn.
  Every screen below is therefore per-item: the queue card lists each item's
  own state, and the job detail opens one item at a time via ?item=<pk>.
* Design never sees money. No view here puts order_value, a rate or a line
  amount into a template context (§2). The queue notice bar says so, and the
  contexts below keep the promise.
* Reserving is not issuing (§10.1·3). Design reserves a sheet; Stores issues
  it later. "Mark Design Approved" is gated on a current drawing AND a live
  reservation, and both are checked here, server-side, not by disabling a
  button.

Nothing is ever "sent" to Production: readiness is derived (drawing + approval
+ issued material), so an item appears in the Production Ready Queue by itself.
The forward panel on the job screen reports that state rather than faking a
handoff button.
"""
from django.contrib import messages
from django.db.models import Max, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import SalesOrder, SOItem
from core.constants import OrderStatus, QueryStatus
from core.decorators import role_required
from core.models import AuditLog
from core.services import log, notify

from .forms import (
    QueryReplyForm, RaiseQueryForm, ReserveSheetForm, RevisionUploadForm,
    SpecForm, available_sheets_for,
)
from .models import DesignQuery, DrawingRevision, MaterialReservation

DESIGN_STATUSES = [OrderStatus.IN_DESIGN, OrderStatus.DESIGN_QUERY]


# ---------------------------------------------------------------- helpers
def item_state(item):
    """The one chip that says where a single item code has got to.

    Read top-down; the first true branch wins, which is also the order the
    designer works in: draw it, approve it, then Stores issues the material.
    """
    if item.is_ready:
        return 'Ready', 'ready'
    if item.design_approved:
        return 'Approved', 'approved'
    if item.current_drawing is not None:
        return 'Drawn', 'drawn'
    return 'No drawing', 'nodrawing'


def item_label(item):
    """What the designer calls this line — its item code, else a position."""
    code = item.item_code
    if code is not None:
        return code.code
    desc = (item.quotation_item.description or '').strip()
    return desc or f'Item #{item.position}'


def _item_row(item, selected_pk=None):
    label, css = item_state(item)
    return {
        'item': item,
        'pk': item.pk,
        'code': item_label(item),
        'position': item.position,
        'state_label': label,
        'state_class': css,
        'needs_work': not item.design_approved,
        'selected': selected_pk is not None and item.pk == selected_pk,
    }


def _design_orders(user):
    """Sales orders sitting at the design stage, scoped to this designer.

    Orders with no designer assigned stay visible to everyone in Design so a
    freshly booked job is never invisible; an admin/superuser sees the lot.
    """
    orders = (
        SalesOrder.objects
        .filter(status__in=DESIGN_STATUSES)
        .select_related('customer', 'quotation')
        .prefetch_related(
            'items__quotation_item__item_code',
            'items__quotation_item__material',
            'items__quotation_item__thickness',
            'items__drawing_revisions',
            'items__reservations',
            'items__job_card_items',
            'items__queries',
            'queries__messages',
        )
    )
    if not user.is_superuser:
        orders = orders.filter(
            Q(assigned_designer=user) | Q(assigned_designer__isnull=True)
        )
    # Newest job first — the designer wants what just landed, not the oldest
    # due date. Urgency is already visible on each card as Expected Delivery.
    return orders.order_by('-booked_on', '-number')


def _open_query(order, item=None):
    """The live query on this job, if any — an open or answered one.

    An order-level query (raised without naming an item) is live for every
    item; an item-level query only for its own item.
    """
    qs = order.queries.exclude(status=QueryStatus.CLOSED)
    if item is not None:
        qs = qs.filter(Q(so_item=item) | Q(so_item__isnull=True))
    return qs.select_related('assigned_to', 'raised_by').first()


def _get_order(so_no):
    """404 on an unknown SO number — deep links must resolve real records."""
    return get_object_or_404(
        SalesOrder.objects.select_related(
            'customer', 'quotation', 'quotation__salesperson',
        ),
        number__iexact=so_no,
    )


def _order_items(order):
    return list(
        order.items
        .select_related(
            'quotation_item', 'quotation_item__item_code',
            'quotation_item__material', 'quotation_item__thickness',
            'quotation_item__finish',
        )
        .prefetch_related('drawing_revisions', 'reservations', 'queries')
    )


def _pick_item(order, items, raw_pk):
    """Resolve ?item=<pk> to one real SOItem of THIS order.

    A bad pk is a 404, not a silent fallback — a stale deep link should say so
    rather than quietly editing a different item. With no pk we open the first
    item that still needs work, which is where the designer left off.
    Returns None only when the order genuinely has no items.
    """
    if raw_pk not in (None, ''):
        raw_pk = str(raw_pk)
        if not raw_pk.isdigit():
            raise Http404('No such item on this order.')
        return get_object_or_404(SOItem, pk=int(raw_pk), order=order)
    if not items:
        return None
    return next((i for i in items if not i.design_approved), items[0])


def _redirect_to_job(order, item=None):
    """POST -> redirect, always back to the exact item that was acted on."""
    url = reverse('design:job_detail', args=[order.number])
    return HttpResponseRedirect(f'{url}?item={item.pk}' if item else url)


def _acted_item(request, so_no):
    """Resolve (order, item) for a POST action. Both must be real."""
    order = _get_order(so_no)
    item = _pick_item(order, _order_items(order), request.POST.get('item'))
    if item is None:
        raise Http404('This sales order has no items.')
    return order, item


def _form_error(request, form):
    """Surface a form's first complaint instead of redirecting in silence."""
    for _field, errs in form.errors.items():
        if errs:
            messages.error(request, errs[0])
            return


# ---------------------------------------------------------------- screens
@role_required('is_designer')
def queue(request):
    """Design Queue — one card per SO, with per-item progress. No prices, ever."""
    cards = []
    counts = {
        'total': 0, 'in_progress': 0, 'query': 0, 'not_started': 0,
        'repeat': 0, 'items_pending': 0,
    }

    for order in _design_orders(request.user):
        items = list(order.items.all())
        rows = [_item_row(i) for i in items]

        drawn = sum(1 for i in items if i.current_drawing is not None)
        approved = sum(1 for i in items if i.design_approved)
        ready = sum(1 for i in items if i.is_ready)
        counts['items_pending'] += len(items) - drawn

        query = _open_query(order)
        started = any(i.design_started_at for i in items)

        if query is not None:
            stage_label, stage_class, card_class = 'Query Raised', 'query-status', 'query'
            counts['query'] += 1
        elif started or drawn:
            stage_label, stage_class, card_class = 'In Progress', 'progress', ''
            counts['in_progress'] += 1
        else:
            stage_label, stage_class, card_class = 'Not Started', 'pending', 'newjob'
            counts['not_started'] += 1

        counts['total'] += 1
        if order.order_type == 'REPEAT':
            counts['repeat'] += 1

        # Where "Start / Continue" lands: the first item still owed work.
        next_item = next((i for i in items if not i.design_approved), None)
        if next_item is None and items:
            next_item = items[0]

        first_msg = ''
        if query is not None:
            msgs = list(query.messages.all())
            first_msg = msgs[0].body if msgs else ''

        cards.append({
            'order': order,
            'rows': rows,
            'total_items': len(items),
            'drawn': drawn,
            # Drawn but not yet approved — the second bar segment, so the two
            # widths add up to the drawn total instead of double counting.
            'drawn_pending': max(drawn - approved, 0),
            'approved': approved,
            'ready': ready,
            'stage_label': stage_label,
            'stage_class': stage_class,
            'card_class': card_class,
            'query': query,
            'query_note': first_msg,
            'started': started,
            'next_item': next_item,
        })

    return render(request, 'design/queue.html', {'cards': cards, 'counts': counts})


@role_required('is_designer')
def job_detail(request, so_no):
    """Every item of one SO. ?item=<pk> chooses which one is open."""
    order = _get_order(so_no)
    items = _order_items(order)
    item = _pick_item(order, items, request.GET.get('item'))

    selected_pk = item.pk if item else None
    rows = [_item_row(i, selected_pk) for i in items]

    # Forward-to-production summary. Readiness is derived, so this panel
    # reports; it does not push anything anywhere.
    ready_rows = [
        {'item': i, 'code': item_label(i)} for i in items if i.is_ready
    ]
    blocked_rows = [
        {'item': i, 'code': item_label(i), 'reason': i.blocked_reason}
        for i in items if not i.is_ready
    ]

    revisions, current, query, reservation = [], None, None, None
    if item is not None:
        revisions = list(item.drawing_revisions.select_related('uploaded_by').all())
        current = next((r for r in revisions if r.is_current), None)
        query = _open_query(order, item) or (
            order.queries.filter(Q(so_item=item) | Q(so_item__isnull=True))
            .select_related('assigned_to', 'raised_by').first()
        )
        reservation = item.reservations.filter(
            released_at__isnull=True,
        ).select_related('sheet', 'sheet__rack', 'reserved_by').first()

    return render(request, 'design/job_detail.html', {
        'so_no': order.number,
        'order': order,
        'item': item,
        'item_code': item_label(item) if item else '',
        'item_rows': rows,
        'items_total': len(items),
        'spec_form': SpecForm(instance=item) if item else None,
        'revisions': revisions,
        'current_revision': current,
        'sheets': available_sheets_for(item),
        'reservation': reservation,
        'has_drawing': current is not None,
        'can_approve': bool(
            item and current is not None and item.material_reserved
            and not item.design_approved
        ),
        'approve_block_reason': _approve_block_reason(item, current),
        'ready_rows': ready_rows,
        'blocked_rows': blocked_rows,
        'query': query,
        'query_messages': (
            query.messages.select_related('author').all() if query else []
        ),
        'audit_entries': (
            AuditLog.objects.filter(object_ref=order.number).select_related('actor')[:25]
        ),
        'prior_orders': (
            SalesOrder.objects.filter(customer=order.customer)
            .exclude(pk=order.pk).count()
        ),
    })


def _approve_block_reason(item, current_revision):
    """Why the approve button is refused — the same test the POST view runs."""
    if item is None:
        return 'This sales order has no items.'
    if item.design_approved:
        return ''
    if current_revision is None and not item.material_reserved:
        return 'Upload a drawing and reserve material first.'
    if current_revision is None:
        return 'Upload a drawing for this item first.'
    if not item.material_reserved:
        return 'Reserve a sheet for this item first.'
    return ''


# ---------------------------------------------------------------- actions
@role_required('is_designer')
@require_POST
def start_design(request, so_no):
    """Stamp design_started_at, which is what flips the queue pill to In Progress."""
    order, item = _acted_item(request, so_no)
    if not item.design_started_at:
        item.design_started_at = timezone.now()
        item.save(update_fields=['design_started_at', 'updated_at'])
        log(request.user, 'SOItem', order.number, 'Design started',
            detail=f'{item_label(item)} (item #{item.position})')
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def save_spec(request, so_no):
    """§10.2·4 — holes / drilling detail, entered by Design."""
    order, item = _acted_item(request, so_no)
    form = SpecForm(request.POST, instance=item)
    if form.is_valid():
        form.save()
        log(request.user, 'SOItem', order.number, 'Design spec saved',
            detail=f'{item_label(item)} · Holes: {item.holes or "—"} '
                   f'· Drilling: {item.drilling_detail or "—"}')
        messages.success(request, f'Holes / drilling saved for {item_label(item)}.')
    else:
        _form_error(request, form)
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def upload_revision(request, so_no):
    """New revision = max + 1 FOR THAT ITEM, marked current (§10.2·3).

    Numbering is per item code, so item 3 can be on Rev 02 while item 7 has no
    drawing at all. Older revisions stay viewable.
    """
    order, item = _acted_item(request, so_no)
    form = RevisionUploadForm(request.POST, request.FILES)
    if form.is_valid():
        highest = item.drawing_revisions.aggregate(m=Max('revision'))['m']
        revision = DrawingRevision.objects.create(
            so_item=item,
            revision=0 if highest is None else highest + 1,
            file=form.cleaned_data['file'],
            uploaded_by=request.user,
            source='Design',
            is_current=True,
            note=form.cleaned_data.get('note', ''),
        )
        if not item.design_started_at:
            item.design_started_at = timezone.now()
            item.save(update_fields=['design_started_at', 'updated_at'])
        log(request.user, 'DrawingRevision', order.number,
            f'{revision.label} uploaded',
            detail=f'{item_label(item)} · {revision.file.name}')
        messages.success(
            request, f'{revision.label} uploaded for {item_label(item)}.'
        )
    else:
        _form_error(request, form)
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def reserve_sheet(request, so_no):
    """Design reserves. Stores issues later — that second step is the real gate."""
    order, item = _acted_item(request, so_no)
    form = ReserveSheetForm(request.POST, item=item)
    if form.is_valid():
        sheet = form.cleaned_data['sheet']
        MaterialReservation.objects.create(
            so_item=item, sheet=sheet, reserved_by=request.user,
        )
        log(request.user, 'MaterialReservation', order.number,
            f'{sheet.uid} reserved',
            detail=f'{item_label(item)} · Awaiting issue by Stores')
        messages.success(
            request,
            f'{sheet.uid} reserved for {item_label(item)}. '
            f'Stores still has to issue it before production can start.',
        )
    else:
        _form_error(request, form)
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def raise_query(request, so_no):
    """Auto-routed to the salesperson who raised the originating quote (§7.1)."""
    order, item = _acted_item(request, so_no)
    form = RaiseQueryForm(request.POST)
    if form.is_valid():
        query = DesignQuery.raise_for(
            order=order, raised_by=request.user,
            subject=form.cleaned_data['subject'],
            body=form.cleaned_data['body'], so_item=item,
        )
        order.refresh_status()
        log(request.user, 'DesignQuery', order.number, 'Query raised',
            detail=f'{item_label(item)} · {query.subject}')
        notify(query.assigned_to, f'Design query on {order.number}: {query.subject}',
               object_ref=order.number)
        messages.success(
            request, f'Query sent to {query.assigned_to.short_name}.'
        )
    else:
        _form_error(request, form)
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def reply_query(request, so_no, query_id):
    order, item = _acted_item(request, so_no)
    query = get_object_or_404(DesignQuery, pk=query_id, order=order)
    form = QueryReplyForm(request.POST, request.FILES)
    if not form.is_valid():
        _form_error(request, form)
    elif query.status == QueryStatus.CLOSED:
        messages.error(request, 'That query is closed — raise a new one instead.')
    else:
        query.reply(request.user, form.cleaned_data['body'],
                    form.cleaned_data.get('attachment'))
        log(request.user, 'DesignQuery', order.number, 'Query reply posted',
            detail=query.subject)
        notify(query.assigned_to, f'Reply on {order.number}: {query.subject}',
               object_ref=order.number)
        messages.success(request, 'Reply posted.')
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def close_query(request, so_no, query_id):
    order, item = _acted_item(request, so_no)
    query = get_object_or_404(DesignQuery, pk=query_id, order=order)
    if query.status != QueryStatus.CLOSED:
        query.close()
        order.refresh_status()
        log(request.user, 'DesignQuery', order.number, 'Query closed',
            detail=query.subject)
        messages.success(request, 'Query closed.')
    return _redirect_to_job(order, item)


@role_required('is_designer')
@require_POST
def approve_design(request, so_no):
    """Per-item approval, gated server-side.

    Refused unless THIS item has both a current drawing and a live material
    reservation. The template also hides the button, but a hand-rolled POST
    must not slip past either — so nothing is written when the gate fails.
    """
    order, item = _acted_item(request, so_no)

    if item.design_approved:
        messages.info(request, f'{item_label(item)} is already approved.')
        return _redirect_to_job(order, item)

    if item.current_drawing is None:
        messages.error(
            request,
            f'Cannot approve {item_label(item)}: no drawing has been uploaded '
            f'for this item yet. Nothing was changed.',
        )
        return _redirect_to_job(order, item)

    if not item.material_reserved:
        messages.error(
            request,
            f'Cannot approve {item_label(item)}: no sheet is reserved for this '
            f'item. Reserve material first. Nothing was changed.',
        )
        return _redirect_to_job(order, item)

    item.design_approved = True
    item.design_approved_at = timezone.now()
    item.design_approved_by = request.user
    item.save(update_fields=[
        'design_approved', 'design_approved_at', 'design_approved_by', 'updated_at',
    ])
    order.refresh_status()
    log(request.user, 'SOItem', order.number, 'Design approved',
        detail=f'{item_label(item)} (item #{item.position})',
        to_value=order.status)
    messages.success(
        request,
        f'{item_label(item)} approved. It reaches the Production Ready Queue '
        f'once Stores issues the reserved sheet.',
    )
    return _redirect_to_job(order, item)
