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
from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Max, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import SalesOrder, SOItem
from core.constants import (
    JOB_FOLDER_ROOT, NCRSource, PROCESS_CARD, PROCESS_UI, OrderStatus, Process,
    ProofOutcome, QueryStatus,
)
from core.decorators import role_required
from core.models import AuditLog
from core.services import log, notify

from quality.models import NCR

from .forms import (
    QueryReplyForm, RaiseQueryForm, ReserveSheetForm, RevisionUploadForm,
    SpecForm, available_sheets_for,
)
from .models import (
    DesignCheck, DesignQuery, DrawingRevision, MaterialReservation, ProofRound,
)

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


def job_folder(item):
    """Where this item's files belong. Derived, never typed.

    One path per item per revision, so "saved in the job folder" is a check a
    designer can actually pass or fail rather than a matter of opinion.
    """
    rev = item.current_drawing.revision if item.current_drawing else 0
    return '\\'.join([JOB_FOLDER_ROOT, item.order.number,
                       item_label(item), f'Rev{rev:02d}']) + '\\'


def _gates(item, checks_done, checks_total):
    """The four gates, in the order they unlock.

    Each one reports whether it is met and whether it is even reachable yet:
    a gate the designer cannot act on is shown locked rather than merely
    unticked, because "not done" and "not your turn" are different problems.
    """
    g1 = item.current_drawing is not None
    g2 = checks_done >= checks_total and checks_total > 0
    g3 = item.proof_approved
    g4 = item.material_reserved
    card = PROCESS_CARD[Process(item.process)]
    return [
        {'n': 1, 'label': 'Design finalised', 'done': g1, 'open': True,
         'detail': (item.current_drawing.label if item.current_drawing
                    else 'No drawing uploaded yet')},
        {'n': 2, 'label': 'Six checks', 'done': g2, 'open': g1,
         'detail': f'{checks_done} of {checks_total}'},
        {'n': 3, 'label': card['gate3'], 'done': g3, 'open': g2,
         'detail': {'approved': 'Approved by customer',
                    'waiting': 'Sent - awaiting customer',
                    'changes': 'Changes requested',
                    'none': 'Not sent yet'}[item.proof_state]},
        {'n': 4, 'label': 'Material reserved', 'done': g4, 'open': g3,
         'detail': 'Sheet reserved' if g4 else 'No sheet reserved'},
    ]


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


# ---------------------------------------------------------------------------
# Design queue, process-aware (spec 07)
# ---------------------------------------------------------------------------
#: What Design is prompted to do next, per process, once the job is open.
PROCESS_ACTION = {
    Process.ETCHING: ('Send visual proof', 'Etching needs customer sign-off on every job'),
    Process.MILLING: ('Apply etch-factor compensation', 'Oversize each side by 1.5 x thickness'),
    Process.LASER: ('Kerf compensation + micro-joints', 'Then export the DXF to the machine folder'),
}


def _sitting(started_at, booked_on=None):
    """How long this job has been sitting, in the queue's own words.

    Measured from when the designer opened it; before that, from when the order
    was booked, because an untouched job has still been waiting.
    """
    if started_at is not None:
        delta = timezone.now() - started_at
        days, seconds = delta.days, delta.seconds
    elif booked_on is not None:
        days, seconds = (timezone.localdate() - booked_on).days, 0
    else:
        return 'New'
    if days >= 1:
        return f'{days} day{"s" if days > 1 else ""}'
    hours = seconds // 3600
    return f'{hours} hr{"s" if hours != 1 else ""}' if hours else 'Just now'


def _next_action(item):
    """The one thing to do next on this item, and why.

    Order matters: a job cannot be proofed before it is drawn, and nesting is
    only worth raising once the proof is in. Returning the FIRST unmet step
    rather than a list is deliberate - a queue that lists everything outstanding
    is a queue nobody reads.
    """
    process = Process(item.process)
    if item.current_drawing is None:
        label, why = PROCESS_ACTION[process]
        if process is Process.ETCHING:
            return 'Start the drawing', 'Nothing drawn yet'
        return f'Start - {label.lower()}', why
    if process is Process.ETCHING and item.proof_state == 'none':
        return 'Send visual proof', 'Drawn, but the customer has not seen it'
    if item.proof_state == 'changes':
        proof = item.current_proof
        return ('Redraw from proof feedback',
                (proof.customer_comment or 'Customer asked for changes')[:70] if proof
                else 'Customer asked for changes')
    if not item.design_approved:
        label, why = PROCESS_ACTION[process]
        return label, why
    if item.awaiting_material:
        return 'Reserve material', 'Design approved - nothing reserved yet'
    return 'Done', 'Approved and material issued'


def _waiting_on(item):
    """(what we are waiting for, with whom) - or None when it is Design's move."""
    if item.proof_state == 'waiting':
        proof = item.current_proof
        rev = f'Rev {proof.revision:02d}' if proof else 'the proof'
        return (f'Customer sign-off on visual proof {rev}',
                item.order.salesperson.short_name
                if item.order.salesperson else 'Sales')
    query = item.order.queries.filter(
        so_item=item, status=QueryStatus.OPEN).select_related('raised_by').first()
    if query is not None:
        return (query.subject or 'Answer to an open design query',
                query.assigned_to.short_name if query.assigned_to else 'Sales')
    return None


def _queue_row(item):
    action, why = _next_action(item)
    state_label, state_class = item_state(item)
    ui = PROCESS_UI[Process(item.process)]
    return {
        'item': item,
        'pk': item.pk,
        'order': item.order,
        'code': item_label(item),
        'process': item.process,
        'process_short': ui['short'],
        'process_mark': ui['mark'],
        'customer': item.order.customer.name,
        'spec': _spec_text(item),
        'sitting': _sitting(item.design_started_at, item.order.booked_on),
        'due': item.order.expected_delivery,
        'overdue': bool(item.order.expected_delivery
                        and item.order.expected_delivery < timezone.localdate()
                        and not item.design_approved),
        'action': action,
        'why': why,
        'state_label': state_label,
        'state_class': state_class,
        'waiting': _waiting_on(item),
        # The spec shows a Folder fact on the headline card. The folder is
        # derived from order/item/revision, so it exists exactly when there is
        # a revision to name it after.
        'folder': 'Created' if item.current_drawing is not None else 'Not yet',
    }


def _spec_text(item):
    bits = [item.material.name if item.material else '',
            item.thickness.name if item.thickness else '']
    size = (item.quotation_item.size or '').strip()
    if size:
        bits.append(size)
    return ' '.join(b for b in bits if b).strip() or '-'


#: WIP ceiling. Not a rule the system enforces - a nudge, as the spec words it.
WIP_LIMIT = 3


def _trend(now, before, higher_is_better=True):
    """This week against last week, as the spec's little arrow.

    Returns (mark, wording). A flat week says so rather than showing an arrow
    with nothing behind it.
    """
    if before is None:
        return '', 'no comparison yet'
    if now == before:
        return '\u25ac', 'same as last week'
    up = now > before
    good = up if higher_is_better else not up
    return ('\u25b2' if good else '\u25bc',
            f'{"up" if up else "down"} from {before}')


def _week_window(weeks_back=0):
    monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    start = monday - timedelta(weeks=weeks_back)
    return start, start + timedelta(days=7)


def _approved_in(user, start, end):
    qs = SOItem.objects.filter(design_approved=True,
                               design_approved_at__date__gte=start,
                               design_approved_at__date__lt=end)
    if not (user.is_superuser or getattr(user, 'is_admin', False)):
        qs = qs.filter(design_approved_by=user)
    return list(qs.select_related('order'))


def _on_time(items):
    dated = [i for i in items if i.order.expected_delivery]
    hit = sum(1 for i in dated
              if i.design_approved_at.date() <= i.order.expected_delivery)
    return hit, len(dated)


def _scorecard(user, rows):
    """This week's numbers, each against last week's. Counted, never estimated."""
    this_start, this_end = _week_window(0)
    last_start, last_end = _week_window(1)

    on_time, dated = _on_time(_approved_in(user, this_start, this_end))
    prev_on_time, prev_dated = _on_time(_approved_in(user, last_start, last_end))

    returned = ProofRound.objects.filter(
        outcome=ProofOutcome.CHANGES,
        responded_at__date__gte=this_start, responded_at__date__lt=this_end).count()
    prev_returned = ProofRound.objects.filter(
        outcome=ProofOutcome.CHANGES,
        responded_at__date__gte=last_start, responded_at__date__lt=last_end).count()

    rework = NCR.objects.filter(
        source=NCRSource.DESIGN,
        raised_at__date__gte=this_start, raised_at__date__lt=this_end).count()
    prev_rework = NCR.objects.filter(
        source=NCRSource.DESIGN,
        raised_at__date__gte=last_start, raised_at__date__lt=last_end).count()

    open_jobs = sum(1 for r in rows if r['state_class'] not in ('ready', 'approved'))
    return {
        'week_start': this_start,
        'week_end': this_end - timedelta(days=1),
        'on_time': on_time, 'dated': dated,
        'on_time_trend': _trend(on_time, prev_on_time if prev_dated else None),
        'returned': returned,
        'returned_trend': _trend(returned, prev_returned, higher_is_better=False),
        'rework': rework,
        'rework_trend': _trend(rework, prev_rework, higher_is_better=False),
        'open_jobs': open_jobs,
        'wip_limit': WIP_LIMIT,
        # The nudge only earns its place when there is actually too much open.
        'wip_over': open_jobs > WIP_LIMIT,
        'open_queries': DesignQuery.objects.filter(status=QueryStatus.OPEN).count(),
    }


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

    # ---- the process-aware layer (spec 07) --------------------------------
    # Built from the same orders, but keyed on the ITEM rather than the order:
    # a designer works one part at a time, and two parts on one SO can be two
    # different processes with two different checks.
    rows = []
    for order in _design_orders(request.user):
        for item in order.items.all():
            if item.design_approved and item.is_ready:
                continue            # finished - it belongs to Production now
            rows.append(_queue_row(item))

    # ---- search and the status chips, applied to the ITEM rows ------------
    # These are what the tables below render, so this is what filtering has to
    # act on. Counts come from the unfiltered list, so a chip always says how
    # many it would show.
    def _status_of(r):
        if r['waiting'] is not None:
            return 'query'
        return 'notstarted' if r['item'].current_drawing is None else 'inprogress'

    counts['total'] = len(rows)
    counts['not_started'] = sum(1 for r in rows if _status_of(r) == 'notstarted')
    counts['in_progress'] = sum(1 for r in rows if _status_of(r) == 'inprogress')
    counts['query'] = sum(1 for r in rows if _status_of(r) == 'query')

    search = (request.GET.get('q') or '').strip()
    if search:
        needle = search.lower()
        rows = [r for r in rows
                if needle in r['code'].lower()
                or needle in r['order'].number.lower()
                or needle in r['customer'].lower()
                or needle in r['spec'].lower()]

    active = (request.GET.get('filter') or 'all').lower()
    if active in ('notstarted', 'inprogress', 'query'):
        rows = [r for r in rows if _status_of(r) == active]
    else:
        active = 'all'

    mine = [r for r in rows if r['waiting'] is None]
    blocked = [r for r in rows if r['waiting'] is not None]

    # Overdue first, then longest-waiting; "start here next" is simply the top
    # of that list, so the headline card and the table can never disagree.
    def _rank(r):
        due = r['due'] or date.max
        return (not r['overdue'], due, -(r['item'].pk))

    mine.sort(key=_rank)
    blocked.sort(key=_rank)

    by_process = []
    for proc in Process:
        hits = [r for r in rows if r['process'] == proc.value]
        ui = PROCESS_UI[proc]
        by_process.append({
            'process': proc.value, 'label': proc.label, 'mark': ui['mark'],
            'blurb': ui['blurb'], 'count': len(hits),
        })

    # The end-of-day nudge: anything open that nobody has touched today.
    today = timezone.localdate()
    stale = [r for r in mine
             if not (r['item'].design_started_at
                     and r['item'].design_started_at.date() == today)]

    context = {
        'counts': counts,
        'search': search,
        'active_filter': active,
        'shown': len(rows),
        'by_process': by_process,
        'next_up': mine[0] if mine else None,
        'my_rows': mine,
        'blocked_rows': blocked,
        'scorecard': _scorecard(request.user, rows),
        'stale': stale,
        'today': today,
    }
    return render(request, 'design/queue.html', context)


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
        # Two distinct things, deliberately not one ladder: what the customer
        # supplied through Sales is the BRIEF, and it stays valid forever;
        # what Design produces is the answer, and only that gets revised.
        all_revs = list(item.drawing_revisions.select_related('uploaded_by').all())
        references = [r for r in all_revs if r.is_customer_reference]
        revisions = [r for r in all_revs if not r.is_customer_reference]
        current = next((r for r in revisions if r.is_current), None)
        query = _open_query(order, item) or (
            order.queries.filter(Q(so_item=item) | Q(so_item__isnull=True))
            .select_related('assigned_to', 'raised_by').first()
        )
        reservation = item.reservations.filter(
            released_at__isnull=True,
        ).select_related('sheet', 'sheet__rack', 'reserved_by').first()

    # ---- the four gates, the checks behind gate 2, and the job folder ----
    checks, checks_done, checks_total, gates, folder, card = [], 0, 0, [], '', {}
    if item is not None:
        checks = DesignCheck.state_for(item)
        checks_done, checks_total = DesignCheck.progress(item)
        gates = _gates(item, checks_done, checks_total)
        folder = job_folder(item)
        card = PROCESS_CARD[Process(item.process)]

    return render(request, 'design/job_detail.html', {
        'checks': checks,
        'checks_done': checks_done,
        'checks_total': checks_total,
        'checks_complete': checks_total > 0 and checks_done >= checks_total,
        'gates': gates,
        'job_folder': folder,
        'card': card,
        'process_label': item.process_label if item is not None else '',
        'process_mark': item.process_ui['mark'] if item is not None else '',
        'so_no': order.number,
        'order': order,
        'item': item,
        'item_code': item_label(item) if item else '',
        'item_rows': rows,
        'items_total': len(items),
        'spec_form': SpecForm(instance=item) if item else None,
        'revisions': revisions,
        'references': references,
        'proof_rounds': list(item.proof_rounds.select_related(
            'sent_by', 'drawing').all()) if item else [],
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

    # A reservation may be swapped freely UNTIL Stores issues it: the sheet is
    # still on the rack, so changing his mind costs nothing. Once issued the
    # plate is physically on the floor and the choice is final.
    existing = item.reservations.filter(released_at__isnull=True).first()
    if existing is not None and existing.issued_at:
        messages.error(
            request,
            f'{existing.sheet.uid} has already been issued to production for '
            f'{item_label(item)} — the material is on the floor, so it can no '
            f'longer be changed here. Ask Stores if the plate must come back.',
        )
        return _redirect_to_job(order, item)

    form = ReserveSheetForm(request.POST, item=item)
    if form.is_valid():
        sheet = form.cleaned_data['sheet']
        if existing is not None:
            # Swap: the old sheet goes back into available stock, or it would
            # sit RESERVED forever against a job that no longer wants it.
            previous = existing.sheet.uid
            existing.release()
            log(request.user, 'MaterialReservation', order.number,
                f'{previous} released', detail=f'{item_label(item)} · swapped for {sheet.uid}')
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


@role_required('is_designer')
@require_POST
def send_proof(request, so_no):
    """Record that an artwork proof has gone out to the customer.

    The proof travels by hand - designer downloads, salesperson walks it to the
    customer. This only writes down that it happened, and opens the round the
    salesperson will close when the answer comes back.
    """
    order, item = _acted_item(request, so_no)

    if item.current_drawing is None:
        messages.error(
            request,
            f'{item_label(item)} has no production drawing yet, so there is '
            f'nothing to send as a proof.')
        return _redirect_to_job(order, item)

    open_round = item.proof_rounds.filter(outcome=ProofOutcome.SENT).first()
    if open_round is not None:
        messages.info(
            request,
            f'{open_round.label} is already out with the customer. Record its '
            f'outcome before sending another.')
        return _redirect_to_job(order, item)

    last = item.proof_rounds.order_by('-revision').first()
    proof = ProofRound.objects.create(
        so_item=item,
        revision=(last.revision + 1) if last else 1,
        drawing=item.current_drawing,
        sent_by=request.user,
    )
    log(request.user, 'ProofRound', order.number, f'{proof.label} sent to customer',
        detail=f'{item_label(item)} - {item.current_drawing.label}')
    messages.success(
        request,
        f'{proof.label} recorded as sent for {item_label(item)}. Sales records '
        f"the customer's answer when it comes back.")
    return _redirect_to_job(order, item)


@require_POST
@role_required('is_designer')
def toggle_check(request, so_no):
    """Tick or untick one of the six pre-release checks.

    Ticking is recorded against the person who did it. Unticking deletes the
    row rather than flagging it, so the card never has to reason about a check
    that was ticked, unticked and ticked again by two different people.
    """
    order = _get_order(so_no)
    item = _pick_item(order, _order_items(order), request.GET.get('item')
                      or request.POST.get('item'))
    if item is None:
        raise Http404('No such item on this order.')

    # The card disables these boxes until a drawing exists; the rule is
    # enforced here as well, because a disabled button is a hint, not a gate.
    if item.current_drawing is None:
        messages.error(
            request,
            'Upload the drawing first — there is nothing to check against yet.')
        return _back_to_item(order, item)

    key = (request.POST.get('key') or '').strip()
    valid = {k for k, _l, _w in DesignCheck.definitions_for(item)}
    if key not in valid:
        # A key from another process, or a stale form - refuse rather than
        # writing a tick that no gate will ever count.
        messages.error(request, 'That check does not belong to this job.')
        return _back_to_item(order, item)

    existing = item.design_checks.filter(key=key).first()
    if existing is not None:
        existing.delete()
        log(request.user, 'SOItem', item_label(item), 'Design check cleared', detail=key)
    else:
        DesignCheck.objects.create(so_item=item, key=key, checked_by=request.user)
        log(request.user, 'SOItem', item_label(item), 'Design check ticked', detail=key)

    return _back_to_item(order, item)


def _back_to_item(order, item):
    url = reverse('design:job_detail', args=[order.number])
    return HttpResponseRedirect(f'{url}?item={item.pk}#checks')
