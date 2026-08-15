"""The notification surface (§9).

core.services.notify() has always written Notification rows; these two views
are what finally lets a user act on them. Both are POST + redirect, like every
other state change in the ERP, and both are role-agnostic — a notification is
addressed to a person, not to a department.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.decorators import landing_url_name, role_label
from core.models import Notification


def _safe_next(request, *candidates):
    """First candidate URL that is genuinely ours, else the user's landing page."""
    for url in candidates:
        if url and url_has_allowed_host_and_scheme(
            url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            return url
    return reverse(landing_url_name(request.user))


@login_required
@require_POST
def notification_read(request, pk):
    """Mark one notification read, then follow wherever it points."""
    # Scoped to the recipient: reading someone else's alert is a 404, not a 403,
    # because the row should not be discoverable at all.
    note = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not note.is_read:
        note.is_read = True
        note.save(update_fields=['is_read', 'updated_at'])
    return redirect(_safe_next(request, note.link, request.META.get('HTTP_REFERER')))


@login_required
@require_POST
def notification_read_all(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect(_safe_next(request, request.META.get('HTTP_REFERER')))


def permission_denied(request, exception=None):
    """The 'No access' screen behind every role_required refusal.

    Django's stock handler renders 403.html with nothing but the exception, so
    the page could not say who you are signed in as or offer a way back. This
    adds both. Signed-out callers are sent to the login page instead — being
    told 'no access' when the real problem is 'not signed in' is a dead end.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse(settings.LOGIN_URL))

    return render(request, '403.html', {
        'role_label': role_label(request.user),
        'landing_url': reverse(landing_url_name(request.user)),
    }, status=403)
