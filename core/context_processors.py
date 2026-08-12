"""Per-request badge counts shared by every screen.

Two numbers are needed all over the ERP and nowhere near the view that happens
to be rendering: how many notifications the signed-in user has not read (the
topbar bell, every role) and how many Design Queries are still OPEN against a
salesperson (their sidebar). Computing them here keeps each Sales view from
re-deriving the same COUNT.

Everything is wrapped in SimpleLazyObject, so a template that never mentions a
value costs no query at all, and an anonymous request costs nothing whatsoever.
"""
from django.utils.functional import SimpleLazyObject

from core.constants import QueryStatus
from core.models import Notification


def _open_query_count(user):
    # Imported here, not at module import time: design imports core.
    from design.models import DesignQuery

    queries = DesignQuery.objects.filter(status=QueryStatus.OPEN)
    if not user.is_superuser:
        queries = queries.filter(assigned_to=user)
    return queries.count()


def badges(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    # Only Sales carries a query inbox; every other role gets a plain 0 without
    # touching the database.
    wants_queries = bool(getattr(user, 'is_salesman', False) or user.is_superuser)

    return {
        'open_query_count': SimpleLazyObject(
            lambda: _open_query_count(user) if wants_queries else 0,
        ),
        'unread_notification_count': SimpleLazyObject(
            lambda: Notification.objects.filter(recipient=user, is_read=False).count(),
        ),
        'recent_notifications': SimpleLazyObject(
            lambda: list(Notification.objects.filter(recipient=user)[:10]),
        ),
    }
