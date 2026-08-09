"""Business rules that more than one app needs.

Keeping the derivations here — rather than in a view or a template — is what
stops the Order Booking dashboard and the Production queue disagreeing about
whether the same job is "delayed".
"""
from datetime import date

from .constants import STATUS_STAGE, Health, OrderStatus, Stage
from .models import AuditLog, Notification


def log(actor, obj_type, obj_ref, action, detail='', from_value='', to_value=''):
    """Record one audit line. Never raises — logging must not break a workflow."""
    return AuditLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        object_type=obj_type,
        object_ref=obj_ref,
        action=action,
        detail=detail,
        from_value=str(from_value or ''),
        to_value=str(to_value or ''),
    )


def notify(recipient, message, link='', object_ref=''):
    if recipient is None:
        return None
    return Notification.objects.create(
        recipient=recipient, message=message, link=link, object_ref=object_ref,
    )


def stage_for(status):
    """Map an order status onto its position on the 7-dot track."""
    return STATUS_STAGE.get(status, Stage.PO_INTAKE)


# Rough working days still needed from each stage to reach dispatch. Used by
# health_for() to decide whether a due date is actually reachable.
DAYS_NEEDED_FROM_STAGE = {
    Stage.PO_INTAKE: 14,
    Stage.DESIGN: 11,
    Stage.MATERIAL: 8,
    Stage.PRODUCTION: 5,
    Stage.QC: 2,
    Stage.DISPATCH: 1,
    Stage.DELIVERED: 0,
}


def health_for(order, today=None):
    """Derive the status badge (§10.2·10).

    Green is the default. Amber is a non-time blocker. Red is purely a timing
    rule — days remaining is less than the work still needed from here — so
    nobody has to remember to flag a job as late.
    """
    today = today or date.today()

    if order.status == OrderStatus.CANCELLED:
        return Health.GRAY
    if order.status == OrderStatus.DISPATCHED:
        return Health.GRAY

    # Non-time blockers first: an explicit hold, or an open design query.
    if order.status == OrderStatus.ON_HOLD:
        return Health.AMBER
    if order.status == OrderStatus.DESIGN_QUERY:
        return Health.AMBER
    if order.has_open_query:
        return Health.AMBER
    if not order.drawing_available:
        return Health.AMBER

    if order.expected_delivery:
        days_left = (order.expected_delivery - today).days
        needed = DAYS_NEEDED_FROM_STAGE.get(stage_for(order.status), 0)
        if days_left < needed:
            return Health.RED

    return Health.GREEN


def is_ready_to_produce(order):
    """§4 gate — drawing approved AND material ISSUED (not merely reserved)."""
    return bool(order.design_approved and order.material_issued)
