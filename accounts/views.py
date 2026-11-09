"""Order Booking (§3.4) — the Accounts screen where a PO becomes a Sales Order.

The table here is deliberately a UNION of two different things, which is why it
cannot be a single queryset:

  * quotations Accounts still have to act on. They show their **QTN** number,
    because §1.1 says the SO number is not minted until Accounts confirm the
    PO — so no SalesOrder row exists yet, and none is invented here; and
  * sales orders already booked. They show their **SO** number plus the
    originating "Ref QTN-xxxx".

Each side is normalised into the small `Row` shape below and the template loops
over that one list. Everything derived (stage, health, badge text) comes from
the model properties in core.services so this screen can never disagree with
the Design queue or the Production dashboard about the same job.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.constants import Health, OrderStatus, OrderType, QuotationStatus, Stage
from core.decorators import role_required
from core.models import AuditLog
from core.pagination import paginate
from core.services import log, notify
from masters.models import ItemCode
from production.models import JobCard
from sales.models import Quotation

from .forms import BookOrderForm, ReasonForm, designer_queryset
from .models import SalesOrder

# Quotations that land on the Accounts desk: the PO is in, or Sales are still
# chasing it. Anything else is not Accounts' business yet.
AWAITING_BOOKING = {QuotationStatus.PO_UPLOADED, QuotationStatus.AWAITING_PO}

# §10.1·5 — Hold and Cancel are pre-dispatch only. Held orders may still be
# cancelled (that is the only way out of a hold other than resuming).
HOLDABLE = set(OrderStatus.pre_dispatch())
CANCELLABLE = set(OrderStatus.pre_dispatch()) | {OrderStatus.ON_HOLD}

HEALTH_CLASS = {
    Health.GREEN: 'green',
    Health.AMBER: 'amber',
    Health.RED: 'red',
    Health.GRAY: 'gray',
}


# ---------------------------------------------------------------- row shape
@dataclass
class Row:
    """One line of the booking table, whatever it was built from."""

    kind: str                      # 'QTN' (not yet booked) or 'SO' (booked)
    pk: int
    number: str
    date: date = None
    customer: str = ''
    type_label: str = ''
    ref_qtn: str = ''
    po: dict = field(default_factory=dict)
    drawing: dict = field(default_factory=dict)
    value: Decimal = Decimal('0')
    value_display: str = ''
    stage: int = Stage.PO_INTAKE
    stage_label: str = ''
    stage_note: str = ''
    expected_delivery: date = None
    health: str = 'gray'
    status_text: str = ''
    reason: str = ''
    salesperson: str = ''
    dots: list = field(default_factory=list)
    row_class: str = ''
    highlight: bool = False        # the pale-blue "ready to book" row
    detail_url: str = ''
    detail_label: str = ''
    order_type: str = OrderType.NEW
    booked_by_id: int = None
    is_on_hold: bool = False
    is_cancelled: bool = False
    can_book: bool = False
    can_reject: bool = False
    can_request_drawing: bool = False
    can_hold: bool = False
    can_cancel: bool = False
    can_resume: bool = False
    book_url: str = ''
    reject_url: str = ''
    drawing_url: str = ''
    hold_url: str = ''
    cancel_url: str = ''
    resume_url: str = ''

    @property
    def has_actions(self):
        return any([
            self.can_book, self.can_reject, self.can_request_drawing,
            self.can_hold, self.can_cancel, self.can_resume, self.detail_url,
        ])

    @property
    def has_kebab(self):
        return self.can_hold or self.can_cancel


# ------------------------------------------------------------- small helpers
def _money(value):
    return f'₹{Decimal(value or 0):,.0f}'


def _compact_money(value):
    """Summary-strip money: 18,60,000 reads better as 18.6L."""
    v = Decimal(value or 0)
    if v >= 10000000:
        return f'₹{v / 10000000:.1f}Cr'
    if v >= 100000:
        return f'₹{v / 100000:.1f}L'
    return f'₹{v:,.0f}'


def _basename(name):
    return (name or '').rsplit('/', 1)[-1]


def _dots(stage):
    """The 7-dot track — one entry per canonical stage (§10.2·10)."""
    return [
        {
            'n': s.value,
            'label': s.label,
            'cls': 'done' if s.value < stage else ('current' if s.value == stage else ''),
            'line': 'done' if s.value < stage else '',
        }
        for s in Stage
    ]


def _po_chip(quotation):
    """The PO chip.

    `number` is carried separately from `name`: Accounts reconcile against the
    customer's own PO number, and the uploaded file is called whatever the
    customer happened to name it.
    """
    number = quotation.po_number or ''
    if quotation.po_file:
        return {
            'name': _basename(quotation.po_file.name),
            'url': quotation.po_file.url,
            'number': number,
        }
    if number:
        return {'name': number, 'url': '', 'number': ''}
    return {'name': '—', 'url': '', 'number': ''}


def _drawing_chip(quotation, order=None):
    """Current drawing revision if Design has one, else the file Sales attached."""
    if order is not None:
        for item in order.items.all():
            revision = item.current_drawing
            if revision:
                # Design can register a revision before the file lands, so fall
                # back to something a human can still recognise the line by.
                name = (
                    _basename(revision.file.name)
                    or str(item.item_code or '')
                    or item.quotation_item.description
                    or f'{order.number} #{item.position}'
                )
                return {
                    'name': name,
                    'rev': f'Rev{revision.revision:02d}',
                    'url': revision.file.url if revision.file else '',
                }
    if quotation.drawing_file:
        return {
            'name': _basename(quotation.drawing_file.name),
            'rev': '',
            'url': quotation.drawing_file.url,
        }
    return {'name': '—', 'rev': '', 'url': ''}


# ------------------------------------------------------------- row builders
def _po_present(quotation):
    """The only thing Accounts need to book: a PO. Drawings come later, from
    Design, one per item code — see SalesOrder.drawing_available."""
    return bool(quotation.po_number or quotation.po_file)


def _quotation_row(quotation):
    """A quotation Accounts have not booked yet — it has no SO number."""
    ready = _po_present(quotation)
    return Row(
        kind='QTN',
        pk=quotation.pk,
        number=quotation.number,
        date=quotation.po_date or quotation.quote_date,
        customer=quotation.customer.name,
        type_label=OrderType(quotation.order_type).label,
        ref_qtn='',
        po=_po_chip(quotation),
        drawing=_drawing_chip(quotation),
        value=quotation.grand_total,
        value_display=_money(quotation.grand_total),
        stage=Stage.PO_INTAKE,
        stage_label='PO Intake',
        stage_note='Ready to book' if ready else 'PO not yet received',
        expected_delivery=quotation.expected_delivery,
        health='gray',
        status_text='Pending booking' if ready else 'Awaiting PO',
        salesperson=quotation.salesperson.short_name,
        dots=_dots(Stage.PO_INTAKE),
        highlight=ready,
        order_type=quotation.order_type,
        # Booking now rests on the PO alone. The drawing is no longer a gate:
        # Design uploads one per item code AFTER the order is booked.
        can_book=ready,
        can_reject=True,
        can_request_drawing=not quotation.has_drawing,
        book_url=reverse('accounts:book_order', args=[quotation.pk]),
        reject_url=reverse('accounts:reject_po', args=[quotation.pk]),
        drawing_url=reverse('accounts:request_drawing', args=[quotation.pk]),
        # No Sales Order exists yet, so "View" opens the QUOTATION — Accounts
        # need its line items and rates to decide whether the PO matches.
        detail_url=reverse('sales:quotation_detail', args=[quotation.pk]),
        detail_label='\U0001f441 View',
    )


def _order_row(order):
    """A booked Sales Order — everything shown is derived, never typed."""
    status = order.status
    on_hold = status == OrderStatus.ON_HOLD
    cancelled = status == OrderStatus.CANCELLED

    if on_hold:
        status_text = 'On hold'
    elif cancelled:
        status_text = 'Cancelled'
    else:
        status_text = order.health_reason

    return Row(
        kind='SO',
        pk=order.pk,
        number=order.number,
        date=order.booked_on or timezone.localtime(order.created_at).date(),
        customer=order.customer.name,
        type_label=OrderType(order.order_type).label,
        ref_qtn=order.quotation.number,
        po=_po_chip(order.quotation),
        drawing=_drawing_chip(order.quotation, order),
        value=order.order_value,
        value_display=_money(order.order_value),
        stage=order.stage,
        stage_label=order.stage_label,
        stage_note=order.stage_note,
        expected_delivery=order.expected_delivery,
        health=HEALTH_CLASS.get(order.health, 'gray'),
        status_text=status_text,
        reason=order.hold_reason if on_hold else (order.cancel_reason if cancelled else ''),
        salesperson=order.salesperson.short_name,
        dots=_dots(order.stage),
        row_class='row-held' if on_hold else ('row-cancelled' if cancelled else ''),
        # Accounts' own order detail — the full item-code master for this SO.
        detail_url=reverse('accounts:order_detail', args=[order.number]),
        detail_label='\U0001f4ac View Query' if order.has_open_query else '\U0001f441 View',
        order_type=order.order_type,
        booked_by_id=order.booked_by_id,
        is_on_hold=on_hold,
        is_cancelled=cancelled,
        can_hold=status in HOLDABLE,
        can_cancel=status in CANCELLABLE,
        can_resume=on_hold,
        hold_url=reverse('accounts:hold_order', args=[order.pk]),
        cancel_url=reverse('accounts:cancel_order', args=[order.pk]),
        resume_url=reverse('accounts:resume_order', args=[order.pk]),
    )


def _build_rows():
    quotations = (
        Quotation.objects
        .filter(status__in=AWAITING_BOOKING)
        .select_related('customer', 'salesperson')
        .prefetch_related('items__tiers')
    )
    orders = (
        SalesOrder.objects
        .select_related('customer', 'quotation', 'quotation__salesperson', 'booked_by')
        .prefetch_related('items__quotation_item', 'items__drawing_revisions',
                          'quotation__items__tiers')
    )

    rows = [_quotation_row(q) for q in quotations] + [_order_row(o) for o in orders]
    # Newest first, quotations and orders interleaved by date. The "Pending
    # booking" chip is how Accounts find what needs them, so the list itself
    # does not need to hoist un-booked quotations to the top.
    rows.sort(key=lambda r: (
        -(r.date.toordinal() if r.date else 0),
        r.number,
    ))
    return rows


# ----------------------------------------------------------------- filtering
FILTERS = ['all', 'approval', 'delayed', 'held', 'onhold', 'cancelled',
           'repeat', 'mine']


def _matches(row, name, user):
    if name == 'approval':
        # Accounts' own to-do list: a quotation sitting on this desk that has
        # not been booked or rejected yet. Booked orders are somebody else's
        # problem, and the mixed All view buries these behind 50-odd rows.
        return row.kind == 'QTN' and not row.is_cancelled
    if name == 'delayed':
        return row.health == 'red'
    if name == 'held':
        return row.health == 'amber'
    if name == 'onhold':
        return row.is_on_hold
    if name == 'cancelled':
        return row.is_cancelled
    if name == 'repeat':
        return row.order_type == OrderType.REPEAT
    if name == 'mine':
        return row.booked_by_id == user.pk
    return True


def _search(row, term):
    haystack = ' '.join([
        row.number, row.ref_qtn, row.customer, row.salesperson,
        row.po.get('name', ''),
    ]).lower()
    return term in haystack


def _summary(rows):
    today = timezone.localdate()
    orders = [r for r in rows if r.kind == 'SO']
    # Value booked this month counts live orders only — a cancelled order is
    # money that never landed.
    booked_this_month = sum(
        (r.value for r in orders
         if r.date and not r.is_cancelled
         and r.date.year == today.year and r.date.month == today.month),
        Decimal('0'),
    )
    return {
        'open_orders': sum(1 for r in orders if not (r.is_cancelled or r.stage == Stage.DELIVERED)),
        'booked_value': _compact_money(booked_this_month),
        'on_track': sum(1 for r in rows if r.health == 'green'),
        'held': sum(1 for r in rows if r.health == 'amber'),
        'delayed': sum(1 for r in rows if r.health == 'red'),
        'new_today': sum(1 for r in rows if r.date == today),
    }


# --------------------------------------------------------------------- views
@role_required('is_accounts')
def order_booking(request):
    rows = _build_rows()

    active = request.GET.get('filter') or 'all'
    if active not in FILTERS:
        active = 'all'
    term = (request.GET.get('q') or '').strip()

    visible = [r for r in rows if _matches(r, active, request.user)]
    if term:
        visible = [r for r in visible if _search(r, term.lower())]

    counts = {name: sum(1 for r in rows if _matches(r, name, request.user)) for name in FILTERS}

    page, pager = paginate(request, visible)
    return render(request, 'accounts/order_booking.html', {
        'rows': page,
        **pager,
        'summary': _summary(rows),
        'counts': counts,
        'active_filter': active,
        'search': term,
        'designers': designer_queryset(),
    })


# ------------------------------------------------- Sales Order — full detail
# The per-item pill colour is driven by SOItem.blocked_reason, not by a second
# copy of the gate rules: amber means "someone still owes us a decision",
# red means "the job physically cannot start".
AMBER_REASONS = {
    'Design query open',
    'Awaiting drawing from Design',
    'Design not approved',
}

# object_type -> (tag shown on the activity row, tag colour class)
LOG_TAGS = {
    'SalesOrder': ('SO', 'so'),
    'SOItem': ('ITEM', 'i2'),
    'DrawingRevision': ('DWG', 'i2'),
    'DesignQuery': ('QUERY', 'i2'),
    'MaterialReservation': ('MAT', 'i1'),
    'JobCard': ('JC', 'i1'),
    'JobCardStation': ('STN', 'i1'),
}


def _item_status(item):
    """(label, pill class, icon) for one row of the item master table."""
    label = item.item_status_label
    if label == 'Dispatched':
        return label, 'done', '✓'
    if label == 'In production':
        return label, 'progress', '⚙'

    reason = item.blocked_reason
    if not reason:                       # nothing in the way — 'Ready'
        return label, 'done', '✓'
    if reason.startswith('On job card'):
        return label, 'progress', '⚙'
    if reason in AMBER_REASONS:
        return label, 'stuck', '⏳'
    return label, 'blocked', '⛔'


def _bucket(label, css):
    """Which segment of the value bar this item's money belongs in."""
    if label == 'Dispatched':
        return 'dispatched'
    if css == 'progress':
        return 'production'
    if css in {'stuck', 'blocked'}:
        return 'blocked'
    return 'ready'


def _spec(qi):
    """Material · thickness · size · finish, skipping whatever is blank."""
    parts = [
        str(qi.material) if qi.material_id else '',
        str(qi.thickness) if qi.thickness_id else '',
        qi.size,
        str(qi.finish) if qi.finish_id else '',
    ]
    return ' · '.join(p for p in parts if p) or (qi.description or '—')


def _drawing(item):
    """The current revision chip, or None while Design still owes one."""
    revision = item.current_drawing
    if revision is None:
        return None
    return {
        'label': revision.label,
        'url': revision.file.url if revision.file else '',
    }


def _material(item):
    """The sheet Stores holds against this item, and how far along it is.

    Two-step by design (§10.1·3): Design RESERVES a sheet, Stores later ISSUES
    it, and only the issue lets the floor start. Showing just "reserved" would
    read as done when the material is still on the rack, so the state is
    always spelled out next to the sheet.
    """
    res = next(
        (r for r in item.reservations.all() if r.released_at is None), None,
    )
    if res is None:
        return {'uid': '', 'state': 'Not reserved', 'css': 'none', 'when': None}

    # Deliberately no link to stores:sheet_detail — that screen is
    # role_required('is_stores'), so linking it from here would hand Accounts,
    # Sales Manager and Production a straight 403.
    issued = res.issued_at is not None
    return {
        'uid': res.sheet.uid,
        'state': 'Issued to floor' if issued else 'Reserved — not issued',
        'css': 'issued' if issued else 'reserved',
        'when': res.issued_at or res.created_at,
        'is_offcut': res.sheet.is_offcut,
    }


def _item_rows(order):
    rows, totals = [], {
        'amount': Decimal('0'),
        'dispatched': Decimal('0'),
        'production': Decimal('0'),
        'blocked': Decimal('0'),
        'ready': Decimal('0'),
    }
    counts = {'dispatched': 0, 'production': 0, 'blocked': 0, 'ready': 0}

    for item in order.items.all():
        qi = item.quotation_item
        label, css, icon = _item_status(item)
        bucket = _bucket(label, css)
        amount = qi.line_amount or Decimal('0')

        totals['amount'] += amount
        totals[bucket] += amount
        counts[bucket] += 1

        card = item.current_job_card
        rows.append({
            'code': qi.item_code.code if qi.item_code_id else (
                qi.description or f'Item #{item.position}'
            ),
            'artwork_ref': qi.artwork_ref,
            'spec': _spec(qi),
            'quantity': qi.quantity,
            'rate': _money(qi.effective_rate),
            'amount': _money(amount),
            'drawing': _drawing(item),
            'material': _material(item),
            'status_label': label,
            'status_class': css,
            'status_icon': icon,
            'job_card': card.number if card else '',
            'job_card_url': reverse('production:job_card', args=[card.number]) if card else '',
            'package_qr': item.package_qr,
        })
    return rows, totals, counts


def _progress(totals, counts):
    """Three (occasionally four) segments of the order value bar."""
    base = totals['amount'] or Decimal('1')

    def pct(key):
        # A plain string, so template localisation can never turn 18.1 into
        # "18,1" and break the inline width:...% .
        return f'{totals[key] / base * 100:.1f}'

    segments = [
        {'key': 'dispatched', 'width': pct('dispatched'), 'colour': 'var(--green)', 'title': 'Dispatched'},
        {'key': 'production', 'width': pct('production'), 'colour': 'var(--blue)', 'title': 'In production'},
        {'key': 'blocked', 'width': pct('blocked'), 'colour': 'var(--red)', 'title': 'Blocked'},
    ]
    if counts['ready']:
        segments.append({
            'key': 'ready', 'width': pct('ready'),
            'colour': 'var(--steel)', 'title': 'Ready — not yet on a job card',
        })

    parts = [
        f"{counts['dispatched']} dispatched",
        f"{counts['production']} in production",
        f"{counts['blocked']} blocked",
    ]
    if counts['ready']:
        parts.append(f"{counts['ready']} ready")
    return segments, ' · '.join(parts)


def _job_cards(order):
    """Every card covering any item of THIS order — a card may span several."""
    cards = (
        JobCard.objects
        .filter(items__so_item__order=order)
        .distinct()
        .prefetch_related(
            'items__so_item__order',
            'items__so_item__quotation_item__item_code',
            'stations',
        )
        .order_by('number')
    )

    total_items = order.items.count()
    out, carded = [], set()
    for card in cards:
        mine = [l for l in card.items.all() if l.so_item.order_id == order.pk]
        carded.update(l.so_item_id for l in mine)

        if card.is_complete:
            status_text, status_class, card_class = 'Completed', 'done', 'done'
        elif card.started_at:
            station = card.current_station
            status_text = station.station.name if station else 'In progress'
            status_class = card_class = 'progress'
        else:
            status_text, status_class, card_class = 'Released', 'progress', ''

        out.append({
            'number': card.number,
            'url': reverse('production:job_card', args=[card.number]),
            'status_text': status_text,
            'status_class': status_class,
            'card_class': 'urgent' if card.is_urgent_split else card_class,
            'created': card.released_at or card.created_at,
            'item_line': f'{len(mine)} of {total_items} items',
            'value': _money(sum((l.so_item.quotation_item.line_amount for l in mine), Decimal('0'))),
            'progress_percent': card.progress_percent,
            'chips': [
                l.so_item.item_code.code if l.so_item.quotation_item.item_code_id
                else f'#{l.so_item.position}'
                for l in mine
            ],
            'is_urgent_split': card.is_urgent_split,
            'urgent_reason': card.urgent_reason,
        })

    pending = [i for i in order.items.all() if i.pk not in carded]
    placeholder = None
    if pending:
        value = sum((i.quotation_item.line_amount for i in pending), Decimal('0'))
        blocked = [i for i in pending if i.blocked_reason]
        placeholder = {
            'count': len(pending),
            'blocked_count': len(blocked),
            'value': _money(value),
        }
    return out, placeholder


def _documents(order):
    """Only chips for things that actually exist — no empty placeholders."""
    quotation = order.quotation
    docs = []

    if quotation.po_file:
        docs.append({
            'tag': 'PO', 'style': '',
            'name': _basename(quotation.po_file.name),
            'meta': f'Received {quotation.po_date:%d %b}' if quotation.po_date else 'Customer PO',
            'url': quotation.po_file.url,
        })
    elif quotation.po_number:
        docs.append({
            'tag': 'PO', 'style': '',
            'name': quotation.po_number,
            'meta': f'Received {quotation.po_date:%d %b}' if quotation.po_date else 'PO number only — no file',
            'url': '',
        })

    docs.append({
        'tag': 'QT', 'style': 'dwg',
        'name': f'{quotation.number} ({QuotationStatus(quotation.status).label})',
        'meta': f'{quotation.items.count()} original items',
        'url': '',
    })

    rate_card = ItemCode.objects.filter(customer=order.customer, is_active=True).count()
    if rate_card:
        docs.append({
            'tag': 'RC', 'style': 'rc',
            'name': f'Rate Card — {order.customer.name}',
            'meta': f'{rate_card} active code{"" if rate_card == 1 else "s"}',
            'url': f"{reverse('masters:item_codes')}?customer={order.customer_id}",
        })
    return docs


def _activity(order):
    entries = (
        AuditLog.objects
        .filter(object_ref=order.number)
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


@role_required('is_accounts', 'is_sales_manager', 'is_production_manager')
def order_detail(request, so_no):
    """Sales Order — Full Detail. Read-only: every number here is derived."""
    order = get_object_or_404(
        SalesOrder.objects
        .select_related('customer', 'quotation', 'quotation__salesperson')
        .prefetch_related(
            'items__quotation_item__item_code',
            'items__quotation_item__material',
            'items__quotation_item__thickness',
            'items__quotation_item__finish',
            'items__quotation_item__tiers',
            'items__drawing_revisions',
            'items__job_card_items__job_card',
            'items__reservations',
            'items__queries',
            'queries',
        ),
        number__iexact=so_no,
    )

    rows, totals, counts = _item_rows(order)
    segments, progress_label = _progress(totals, counts)
    cards, placeholder = _job_cards(order)

    item_count = len(rows)
    active_cards = sum(1 for c in cards if c['status_class'] != 'done')
    if item_count:
        badge = (
            f'{order.get_status_display()} — '
            f'{counts["dispatched"]} of {item_count} items dispatched'
        )
    else:
        badge = f'{order.get_status_display()} — no items on this order'

    return render(request, 'accounts/order_detail.html', {
        'order': order,
        'badge_text': badge,
        'badge_class': HEALTH_CLASS.get(order.health, 'gray'),
        'po_chip': _po_chip(order.quotation),
        'meta': {
            'customer': order.customer.name,
            'salesperson': order.salesperson.short_name,
            'order_value': _money(order.order_value),
            'item_codes': f'{item_count} Total' if item_count else 'None',
            'job_cards': (
                f'{len(cards)} Total · {active_cards} Active' if cards else 'None yet'
            ),
            'order_type': OrderType(order.order_type).label,
        },
        'rows': rows,
        'items_total': _money(totals['amount']),
        'item_count': item_count,
        'segments': segments,
        'progress_label': progress_label,
        'cards': cards,
        'placeholder': placeholder,
        'documents': _documents(order),
        'activity': _activity(order),
    })


def _back(request):
    """Every state change is POST-then-redirect, back to the list."""
    return redirect(reverse('accounts:order_booking'))


@role_required('is_accounts')
@require_POST
def book_order(request, pk):
    """Confirm the PO — this is where the Sales Order number is minted (§1.1)."""
    quotation = get_object_or_404(
        Quotation.objects.select_related('customer', 'salesperson'), pk=pk,
    )

    if quotation.status not in AWAITING_BOOKING:
        messages.error(request, f'{quotation.number} is not awaiting booking.')
        return _back(request)
    # The drawing is NOT a booking gate any more: the PO is common to the
    # whole order, and Design uploads one drawing per item code once the SO
    # exists. Only the PO is checked here.
    if not _po_present(quotation):
        messages.error(
            request,
            f'{quotation.number} has no PO on file — Accounts cannot book an '
            f'order without one.',
        )
        return _back(request)

    form = BookOrderForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Could not book the order: check the delivery date and designer.')
        return _back(request)

    designer = form.cleaned_data['designer']
    order = SalesOrder.mint_from_quotation(
        quotation, request.user,
        expected_delivery=form.cleaned_data['expected_delivery'],
        designer=designer,
    )

    note = form.cleaned_data['design_note']
    if note:
        order.design_note = note
        order.save(update_fields=['design_note', 'updated_at'])

    log(
        request.user, 'SalesOrder', order.number, 'BOOKED',
        detail=f'Booked from {quotation.number} for {quotation.customer.name}.',
        from_value=quotation.number, to_value=order.number,
    )
    if designer:
        notify(
            designer,
            f'{order.number} ({order.customer.name}) assigned to you for design.',
            link=reverse('design:job_detail', args=[order.number]),
            object_ref=order.number,
        )
    notify(
        quotation.salesperson,
        f'{quotation.number} booked as {order.number} — sent to Design.',
        object_ref=order.number,
    )
    messages.success(
        request,
        f'{quotation.number} confirmed. Sales Order {order.number} created and sent to Design'
        + (f' — {designer.short_name} notified.' if designer else ' — awaiting designer allocation.'),
    )
    return _back(request)


@role_required('is_accounts')
@require_POST
def reject_po(request, pk):
    """Send the PO back to Sales. The reason is mandatory, server-side."""
    quotation = get_object_or_404(Quotation.objects.select_related('salesperson'), pk=pk)

    if quotation.status not in AWAITING_BOOKING:
        messages.error(request, f'{quotation.number} is not awaiting booking.')
        return _back(request)

    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'A reason is required to reject a PO. Nothing was changed.')
        return _back(request)

    reason = form.cleaned_data['reason']
    previous = quotation.status
    quotation.status = QuotationStatus.PO_REJECTED
    quotation.po_rejection_reason = reason
    quotation.po_rejected_by = request.user
    quotation.po_rejected_at = timezone.now()
    quotation.save(update_fields=[
        'status', 'po_rejection_reason', 'po_rejected_by', 'po_rejected_at', 'updated_at',
    ])

    log(
        request.user, 'Quotation', quotation.number, 'PO_REJECTED',
        detail=reason, from_value=previous, to_value=QuotationStatus.PO_REJECTED,
    )
    notify(
        quotation.salesperson,
        f'PO on {quotation.number} rejected by Accounts: {reason}',
        object_ref=quotation.number,
    )
    messages.success(request, f'PO on {quotation.number} rejected — {quotation.salesperson.short_name} notified.')
    return _back(request)


@role_required('is_accounts')
@require_POST
def request_drawing(request, pk):
    """Nudge Sales for the customer's reference drawing.

    Kept deliberately: it is a courtesy chase, NOT a booking gate. The order
    can be booked on the PO alone; the drawings that matter are the per-item
    revisions Design uploads after booking.
    """
    quotation = get_object_or_404(Quotation.objects.select_related('salesperson'), pk=pk)

    notify(
        quotation.salesperson,
        f'Accounts are asking for the customer reference drawing on '
        f'{quotation.number} ({quotation.customer.name}).',
        object_ref=quotation.number,
    )
    log(
        request.user, 'Quotation', quotation.number, 'DRAWING_REQUESTED',
        detail=f'Reminder sent to {quotation.salesperson.short_name}.',
    )
    messages.success(
        request,
        f'Reminder sent to {quotation.salesperson.short_name} for the reference '
        f'drawing on {quotation.number}. Booking is not blocked by it.',
    )
    return _back(request)


@role_required('is_accounts')
@require_POST
def hold_order(request, pk):
    """§10.1·5 — resumable pause. Reason mandatory; pre-dispatch only."""
    order = get_object_or_404(SalesOrder.objects.select_related('quotation'), pk=pk)

    if order.status in OrderStatus.terminal() or order.status not in HOLDABLE:
        messages.error(
            request,
            f'{order.number} cannot be held from status "{order.get_status_display()}".',
        )
        return _back(request)

    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'A reason is required to put an order on hold. Nothing was changed.')
        return _back(request)

    reason = form.cleaned_data['reason']
    previous = order.status
    order.put_on_hold(reason)

    log(
        request.user, 'SalesOrder', order.number, 'ON_HOLD',
        detail=reason, from_value=previous, to_value=OrderStatus.ON_HOLD,
    )
    for user in {order.salesperson, order.assigned_designer}:
        notify(user, f'{order.number} put on hold by Accounts: {reason}', object_ref=order.number)
    messages.success(request, f'{order.number} put on hold. Reason logged to the audit trail.')
    return _back(request)


@role_required('is_accounts')
@require_POST
def resume_order(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)

    if order.status != OrderStatus.ON_HOLD:
        messages.error(request, f'{order.number} is not on hold.')
        return _back(request)

    previous = order.status_before_hold
    order.resume()

    log(
        request.user, 'SalesOrder', order.number, 'RESUMED',
        detail='Hold lifted.', from_value=OrderStatus.ON_HOLD, to_value=order.status,
    )
    for user in {order.salesperson, order.assigned_designer}:
        notify(user, f'{order.number} resumed by Accounts.', object_ref=order.number)
    messages.success(
        request,
        f'{order.number} resumed — restored to "{order.get_status_display()}".'
        if previous else f'{order.number} resumed.',
    )
    return _back(request)


@role_required('is_accounts')
@require_POST
def cancel_order(request, pk):
    """Terminal. Reserved material is released back to stock by cancel()."""
    order = get_object_or_404(SalesOrder.objects.select_related('quotation'), pk=pk)

    if order.status in OrderStatus.terminal() or order.status not in CANCELLABLE:
        messages.error(
            request,
            f'{order.number} cannot be cancelled from status "{order.get_status_display()}".',
        )
        return _back(request)

    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'A reason is required to cancel an order. Nothing was changed.')
        return _back(request)

    reason = form.cleaned_data['reason']
    previous = order.status
    order.cancel(reason)

    log(
        request.user, 'SalesOrder', order.number, 'CANCELLED',
        detail=reason, from_value=previous, to_value=OrderStatus.CANCELLED,
    )
    for user in {order.salesperson, order.assigned_designer}:
        notify(user, f'{order.number} cancelled by Accounts: {reason}', object_ref=order.number)
    messages.success(
        request,
        f'{order.number} cancelled. Any reserved material has been released back to available stock.',
    )
    return _back(request)
