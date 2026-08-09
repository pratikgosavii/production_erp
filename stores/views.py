"""Stores screens: raw material inventory, sheet logging, QR labels, scanning.

Everything here is gated on the Stores role. The two state changes Stores owns
— issuing reserved material and confirming an outward dispatch — are POST-only
and re-checked server-side; the buttons on the dashboard are a convenience, not
the rule (§10.1·3, §10.1·8).
"""
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.constants import SheetStatus
from core.decorators import role_required
from core.models import Sequence
from core.services import log
from masters.models import ReorderLevel

from .forms import (
    MAX_BATCH, UNASSIGNED, BlankBatchForm, SheetDetailForm, SheetLogForm,
    active_lookups,
)
from .models import DispatchRecord, Sheet

STORES = role_required('is_stores')

# Chip -> queryset filter on the inventory table.
STATUS_CHIPS = {
    'available': SheetStatus.AVAILABLE,
    'reserved': SheetStatus.RESERVED,
    'issued': SheetStatus.ISSUED,
}


def _sheet_queryset():
    return Sheet.objects.select_related(
        'material', 'thickness', 'grade', 'size', 'finish', 'supplier', 'rack', 'parent',
    ).prefetch_related('reservations__so_item__order__customer', 'reservations__reserved_by')


def _below_reorder():
    """Every material+thickness whose AVAILABLE count sits under its minimum.

    Reorder levels are Admin's to set (§10.2·6); Stores only reads them.
    """
    have = {
        (row['material'], row['thickness']): row['n']
        for row in _sheet_queryset().filter(status=SheetStatus.AVAILABLE)
        .values('material', 'thickness').annotate(n=Count('id'))
    }
    rows = []
    for level in ReorderLevel.objects.select_related('material', 'thickness'):
        count = have.get((level.material_id, level.thickness_id), 0)
        if count < level.minimum_qty:
            rows.append({
                'material': level.material, 'thickness': level.thickness,
                'have': count, 'minimum': level.minimum_qty,
                'key': (level.material_id, level.thickness_id),
            })
    return rows


@STORES
def dashboard(request):
    """Material inventory + the outward dispatch queue."""
    sheets = _sheet_queryset()

    q = (request.GET.get('q') or '').strip()
    material_id = (request.GET.get('material') or '').strip()
    kind = (request.GET.get('kind') or '').strip()
    chip = (request.GET.get('status') or '').strip()

    low_rows = _below_reorder()

    filtered = sheets
    if q:
        filtered = filtered.filter(
            Q(uid__icontains=q) | Q(material__name__icontains=q)
            | Q(thickness__name__icontains=q) | Q(rack__name__icontains=q),
        )
    if material_id.isdigit():
        filtered = filtered.filter(material_id=int(material_id))
    if kind == 'sheet':
        filtered = filtered.filter(is_offcut=False)
    elif kind == 'offcut':
        filtered = filtered.filter(is_offcut=True)

    if chip in STATUS_CHIPS:
        filtered = filtered.filter(status=STATUS_CHIPS[chip])
    elif chip == 'low':
        low_q = Q(pk__in=[])
        for row in low_rows:
            low_q |= Q(material_id=row['key'][0], thickness_id=row['key'][1])
        filtered = filtered.filter(low_q, status=SheetStatus.AVAILABLE)

    # Counts are of the whole register, not of the current filter — the strip
    # is a stock summary, not a result count.
    by_status = {
        row['status']: row['n']
        for row in Sheet.objects.values('status').annotate(n=Count('id'))
    }
    today = timezone.localdate()
    consumed_this_month = Sheet.objects.filter(
        status=SheetStatus.CONSUMED, updated_at__year=today.year,
        updated_at__month=today.month,
    ).count()

    dispatches = DispatchRecord.objects.select_related(
        'order__customer', 'dispatched_by',
    ).prefetch_related('order__items__quotation_item')

    return render(request, 'stores/dashboard.html', {
        'sheets': filtered,
        'summary': {
            'total': sum(by_status.values()),
            'available': by_status.get(SheetStatus.AVAILABLE, 0),
            'reserved': by_status.get(SheetStatus.RESERVED, 0),
            'issued': by_status.get(SheetStatus.ISSUED, 0),
            'consumed_this_month': consumed_this_month,
            'offcuts': Sheet.objects.filter(is_offcut=True).count(),
            'below_reorder': len(low_rows),
        },
        'low_rows': low_rows,
        'dispatches': dispatches,
        'dispatch_pending': dispatches.filter(dispatched_at__isnull=True).count(),
        'materials': active_lookups()['materials'],
        'q': q, 'material_id': material_id, 'kind': kind, 'chip': chip,
        'unassigned': UNASSIGNED,
    })


def _safe_next(request, fallback='stores:dashboard'):
    """Only ever bounce back to a path on this site."""
    nxt = request.POST.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return nxt
    return reverse(fallback)


@STORES
@require_POST
def issue_sheet(request, uid):
    """Reserved -> Issued. Only Design's reservation makes a sheet issuable."""
    sheet = get_object_or_404(Sheet, uid=uid)
    reservation = sheet.active_reservation

    if reservation is None or not sheet.can_be_issued:
        messages.error(
            request,
            f'{sheet.uid} cannot be issued — material must be reserved by Design first.',
        )
        return redirect(_safe_next(request))

    reservation.issue(request.user)
    log(request.user, 'Sheet', sheet.uid, 'Issued to production',
        detail=f'Against {reservation.so_item.order.number}',
        from_value=SheetStatus.RESERVED, to_value=SheetStatus.ISSUED)
    messages.success(
        request,
        f'{sheet.uid} issued against {reservation.so_item.order.number}.',
    )
    return redirect(_safe_next(request))


@STORES
@require_POST
def mark_dispatched(request, pk):
    """§10.1·8 — the store in-charge confirms the physical outward."""
    record = get_object_or_404(DispatchRecord.objects.select_related('order'), pk=pk)
    if record.is_dispatched:
        messages.error(request, f'{record.order.number} is already dispatched.')
    else:
        record.dispatched_at = timezone.now()
        record.dispatched_by = request.user
        record.save(update_fields=['dispatched_at', 'dispatched_by', 'updated_at'])
        log(request.user, 'SalesOrder', record.order.number, 'Marked dispatched')
        messages.success(request, f'{record.order.number} marked dispatched.')
    return redirect('stores:dashboard')


def _new_label_batch():
    """BATCH-2026-0241 — one number per print run, so labels stay traceable.

    The counter is nudged past any batch already printed on a sticker before it
    is drawn from, so a stack of labels in the store can never be shadowed by a
    fresh batch carrying the same number.
    """
    prefix = f'BATCH-{timezone.localdate().year}'
    last = Sheet.objects.filter(
        label_batch__startswith=f'{prefix}-',
    ).order_by('-label_batch').first()
    highest = 0
    if last:
        tail = last.label_batch.rsplit('-', 1)[-1]
        highest = int(tail) if tail.isdigit() else 0

    seq = Sequence.objects.filter(prefix=prefix).first()
    if seq and seq.last_value < highest:
        Sequence.objects.filter(pk=seq.pk).update(last_value=highest)
    return Sequence.next_number(prefix, padding=4, start=highest)


@STORES
def log_sheet(request):
    """Two ways in: a blank QR batch, or a fully detailed lot."""
    blank_form = BlankBatchForm()
    full_form = SheetLogForm()
    mode = request.POST.get('mode') if request.method == 'POST' else (
        request.GET.get('mode') or 'a'
    )

    if request.method == 'POST':
        if mode == 'b':
            full_form = SheetLogForm(request.POST)
            if full_form.is_valid():
                with transaction.atomic():
                    batch = _new_label_batch()
                    sheets = full_form.create_sheets(request.user, batch)
                log(request.user, 'Sheet', batch,
                    f'Logged {len(sheets)} sheets with details')
                messages.success(
                    request, f'{len(sheets)} sheets added to stock as {batch}.')
                return redirect(f"{_labels_url()}?batch={batch}")
        else:
            mode = 'a'
            blank_form = BlankBatchForm(request.POST)
            if blank_form.is_valid():
                with transaction.atomic():
                    batch = _new_label_batch()
                    sheets = blank_form.create_sheets(request.user, batch)
                log(request.user, 'Sheet', batch,
                    f'Reserved {len(sheets)} blank sheet UIDs')
                messages.success(
                    request, f'{len(sheets)} blank labels reserved as {batch}.')
                return redirect(f"{_labels_url()}?batch={batch}")

    context = {
        'blank_form': blank_form,
        'full_form': full_form,
        'mode': mode if mode in ('a', 'b') else 'a',
        'max_batch': MAX_BATCH,
        'today': timezone.localdate(),
        'next_uid_hint': _next_uid_hint(),
    }
    context.update(active_lookups())
    return render(request, 'stores/log_sheet.html', context)


def _labels_url():
    return reverse('stores:qr_labels')


def _next_uid_hint():
    """What the next SHT- number will be. Read-only — nothing is reserved."""
    seq = Sequence.objects.filter(prefix='SHT').first()
    nxt = (seq.last_value + 1) if seq else 101
    return f'SHT-{nxt:04d}'


@STORES
def qr_labels(request):
    """Print sheet for a real set of sheets — ?batch=… or ?ids=SHT-0101,…"""
    batch = (request.GET.get('batch') or '').strip()
    ids = (request.GET.get('ids') or '').strip()

    sheets = Sheet.objects.select_related('material', 'thickness').order_by('uid')
    if batch:
        sheets = sheets.filter(label_batch=batch)
    elif ids:
        wanted = [part.strip().upper() for part in ids.split(',') if part.strip()]
        sheets = sheets.filter(uid__in=wanted)
    else:
        sheets = sheets.none()

    labels = [{
        'uid': s.uid,
        'material': '' if s.material.name == UNASSIGNED else (
            f'{s.material.name} · {s.thickness.name}'
        ),
        'date': s.received_on or s.created_at.date(),
        'url': s.scan_url,
    } for s in sheets]

    generated_by = ''
    first = sheets.first()
    if first and first.logged_by:
        generated_by = first.logged_by.short_name

    return render(request, 'stores/qr_labels.html', {
        'labels': labels,
        'batch': batch or (first.label_batch if first else ''),
        'mode_label': 'Blank — fill on scan' if labels and not labels[0]['material']
                      else ('Details logged' if labels else 'Nothing selected'),
        'generated_on': first.created_at if first else timezone.now(),
        'generated_by': generated_by or request.user.short_name,
    })


@STORES
def scan(request):
    """Camera scanner plus the manual fallback."""
    recent = Sheet.objects.filter(last_scanned_at__isnull=False).order_by(
        '-last_scanned_at')[:6]
    if not recent:
        recent = Sheet.objects.order_by('-created_at')[:6]
    return render(request, 'stores/scan.html', {
        'recent': recent,
        'scan_error': request.session.pop('scan_error', ''),
    })


@STORES
@require_POST
def scan_open(request):
    """Resolve a typed sheet number. Unknown ones come back with a message."""
    uid = (request.POST.get('uid') or '').strip().upper()
    if not uid:
        request.session['scan_error'] = 'Enter a sheet number, e.g. SHT-0101.'
        return redirect('stores:scan')
    if not Sheet.objects.filter(uid=uid).exists():
        request.session['scan_error'] = (
            f'No sheet numbered {uid} exists in the register. '
            'Check the label, or log the sheet first.'
        )
        return redirect('stores:scan')
    return redirect('stores:sheet_detail', uid=uid)


@STORES
def sheet_detail(request, uid):
    """The form a scan opens. Status is shown, never set here."""
    sheet = get_object_or_404(_sheet_queryset(), uid=uid)

    if request.method == 'POST':
        form = SheetDetailForm(request.POST, request.FILES, instance=sheet)
        if form.is_valid():
            form.save()
            log(request.user, 'Sheet', sheet.uid, 'Sheet details updated')
            messages.success(request, f'{sheet.uid} updated. Status unchanged — it is set by workflow.')
            return redirect('stores:sheet_detail', uid=sheet.uid)
    else:
        form = SheetDetailForm(instance=sheet)
        # A GET on this URL is what a scan looks like.
        Sheet.objects.filter(pk=sheet.pk).update(
            last_scanned_at=timezone.now(), last_scanned_by=request.user,
        )
        sheet.refresh_from_db(fields=['last_scanned_at', 'last_scanned_by'])

    context = {
        'sheet': sheet,
        'form': form,
        'uid': sheet.uid,
        'reservations': sheet.reservations.select_related(
            'so_item__order', 'reserved_by', 'issued_by', 'consumed_by',
        ).all(),
        'is_unassigned': sheet.material.name == UNASSIGNED,
        'today': date.today(),
    }
    context.update(active_lookups())
    return render(request, 'stores/sheet_detail.html', context)
