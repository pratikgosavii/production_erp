"""Quality screens — the NCR / CAPA register (§12).

Open to QA, Production Management and Admin. A non-conformance is a
cross-department question: the person who has to answer it is rarely the
person who logged it, so the register is not scoped to one role's own records.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from datetime import timedelta

from django.utils import timezone
from django.views.decorators.http import require_POST
from functools import wraps

from django.contrib.auth.decorators import login_required

from core.constants import NCRSource, NCRStatus, Severity
from core.services import log

from .models import NCR


def quality_required(view):
    """QA owns this, Production answers most of it, Admin sees everything."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        u = request.user
        if (u.is_staff or u.is_superuser or u.is_qa
                or u.is_production_manager or u.is_floor_manager):
            return view(request, *args, **kwargs)
        raise PermissionDenied('The NCR register is QA, Production and Admin.')
    return wrapper


FILTERS = [
    ('open', 'Open'),
    ('overdue', 'Overdue'),
    ('no_capa', 'Awaiting root cause'),
    ('closed', 'Closed'),
    ('all', 'All'),
]


def _keep(ncr, key):
    if key == 'all':
        return True
    if key == 'open':
        return ncr.is_open
    if key == 'overdue':
        return ncr.is_overdue
    if key == 'no_capa':
        return ncr.is_open and not ncr.has_capa
    return not ncr.is_open


@quality_required
def register(request):
    """Every non-conformance, newest first."""
    rows = list(
        NCR.objects
        .select_related('job_card_station__job_card', 'job_card_station__station',
                        'so_item__order__customer', 'raised_by', 'owner', 'closed_by')
        .all()
    )

    active = (request.GET.get('filter') or 'open').lower()
    if active not in dict(FILTERS):
        active = 'open'

    chips = [
        {'key': k, 'label': l, 'count': sum(1 for n in rows if _keep(n, k)),
         'is_active': k == active}
        for k, l in FILTERS
    ]

    return render(request, 'quality/register.html', {
        'rows': [n for n in rows if _keep(n, active)],
        'chips': chips,
        'active_filter': active,
        'summary': {
            'open': sum(1 for n in rows if n.is_open),
            'overdue': sum(1 for n in rows if n.is_overdue),
            'no_capa': sum(1 for n in rows if n.is_open and not n.has_capa),
            'closed': sum(1 for n in rows if not n.is_open),
        },
        'statuses': NCRStatus.choices,
        'severities': Severity.choices,
        'sources': NCRSource.choices,
        'nav': 'ncr',
    })


@quality_required
@require_POST
def save_capa(request, pk):
    """Record the thinking: root cause, what was done, what stops a repeat."""
    ncr = get_object_or_404(NCR, pk=pk)
    if not ncr.is_open:
        messages.info(request, f'{ncr.number} is closed.')
        return redirect('quality:register')

    ncr.root_cause = (request.POST.get('root_cause') or '').strip()
    ncr.corrective_action = (request.POST.get('corrective_action') or '').strip()
    ncr.preventive_action = (request.POST.get('preventive_action') or '').strip()

    status = (request.POST.get('status') or '').strip()
    if status in dict(NCRStatus.choices) and status != NCRStatus.CLOSED:
        ncr.status = status
    elif ncr.status == NCRStatus.OPEN and ncr.root_cause:
        # Writing a cause is itself the move out of "nobody has looked".
        ncr.status = NCRStatus.INVESTIGATING

    ncr.save(update_fields=['root_cause', 'corrective_action', 'preventive_action',
                            'status', 'updated_at'])
    log(request.user, 'NCR', ncr.number, 'CAPA updated',
        detail=ncr.root_cause[:160])
    messages.success(request, f'{ncr.number} updated.')
    return redirect('quality:register')


@quality_required
@require_POST
def close(request, pk):
    """Close it — refused unless the cause and the action are both written."""
    ncr = get_object_or_404(NCR, pk=pk)
    try:
        ncr.close(request.user, request.POST.get('verification_note') or '')
    except ValidationError as exc:
        messages.error(request, f'{ncr.number} not closed — {exc.messages[0]}')
        return redirect('quality:register')

    log(request.user, 'NCR', ncr.number, 'NCR closed',
        detail=ncr.verification_note[:160])
    messages.success(request, f'{ncr.number} closed.')
    return redirect('quality:register')


@quality_required
@require_POST
def raise_manual(request):
    """Open an NCR by hand — a complaint, a supplier issue, an audit finding."""
    title = (request.POST.get('title') or '').strip()
    source = (request.POST.get('source') or '').strip()
    if not title:
        messages.error(request, 'An NCR needs a title saying what went wrong.')
        return redirect('quality:register')
    if source not in dict(NCRSource.choices):
        messages.error(request, 'Say where this non-conformance came from.')
        return redirect('quality:register')

    severity = (request.POST.get('severity') or Severity.MINOR).strip()
    ncr = NCR.objects.create(
        source=source,
        severity=severity if severity in dict(Severity.choices) else Severity.MINOR,
        title=title,
        detail=(request.POST.get('detail') or '').strip(),
        raised_by=request.user,
        due_date=timezone.localdate() + timedelta(days=14),
    )
    log(request.user, 'NCR', ncr.number, 'NCR raised', detail=ncr.title)
    messages.success(request, f'{ncr.number} raised.')
    return redirect('quality:register')
