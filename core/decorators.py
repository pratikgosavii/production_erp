"""Role gating (§2).

Access is enforced here, server-side. Hiding a link in a template is not
access control — the Design role must be blocked from price fields even if
someone types the URL directly.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*flags):
    """Allow the view only for users holding one of the given role flags.

    Superusers always pass, so Admin can inspect any screen.
    """
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_superuser or any(getattr(user, f, False) for f in flags):
                return view(request, *args, **kwargs)
            raise PermissionDenied(
                'Your role does not have access to this screen.'
            )
        return wrapper
    return decorator


# Where each role lands after login. Order matters for users holding two roles.
ROLE_LANDING = [
    ('is_salesman', 'sales:dashboard'),
    ('is_sales_manager', 'sales:manager_approvals'),
    ('is_accounts', 'accounts:order_booking'),
    ('is_designer', 'design:queue'),
    ('is_production_manager', 'production:dashboard'),
    # NOT production:dashboard — that screen is is_production_manager-only, so
    # sending the floor there meant signing in landed straight on "No access".
    ('is_floor_manager', 'production:floor_queue'),
    ('is_stores', 'stores:dashboard'),
    ('is_qa', 'stores:dashboard'),  # QA module deferred (§3.10)
]


ROLE_LABELS = [
    ('is_salesman', 'Sales'),
    ('is_sales_manager', 'Sales Manager'),
    ('is_accounts', 'Accounts'),
    ('is_designer', 'Design'),
    ('is_production_manager', 'Production Manager'),
    ('is_floor_manager', 'Floor Manager'),
    ('is_stores', 'Stores'),
    ('is_qa', 'QA'),
    ('is_operator', 'Operator'),
]


def role_label(user):
    """Human name for the roles a user holds — for the no-access screen."""
    if not user.is_authenticated:
        return ''
    held = [label for flag, label in ROLE_LABELS if getattr(user, flag, False)]
    if user.is_superuser:
        held.append('Admin')
    return ' · '.join(held) or 'no role assigned'


def landing_url_name(user):
    if user.is_superuser and not any(getattr(user, f, False) for f, _ in ROLE_LANDING):
        return 'admin:index'
    for flag, url_name in ROLE_LANDING:
        if getattr(user, flag, False):
            return url_name
    return 'admin:index'
