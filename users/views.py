from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import reverse

from core.decorators import landing_url_name, role_label

#: The login form's role chips, mapped to the flag each one claims. The keys
#: are the data-role values in users/login.html — keep the two in step.
LOGIN_ROLE_FLAGS = {
    'sales': ('is_salesman', 'Sales'),
    'sales_manager': ('is_sales_manager', 'Sales Manager'),
    'accounts': ('is_accounts', 'Accounts'),
    'design': ('is_designer', 'Design'),
    'production_manager': ('is_production_manager', 'Production Manager'),
    'floor_manager': ('is_floor_manager', 'Floor Manager'),
    'stores': ('is_stores', 'Stores'),
    'qa': ('is_qa', 'QA'),
}


def _chip_for(user):
    """The chip this user should have picked — first role they actually hold."""
    for key, (flag, _label) in LOGIN_ROLE_FLAGS.items():
        if getattr(user, flag, False):
            return key
    return ''


def login_view(request):
    """Username + password only — OTP was dropped (§3.1).

    The role chip is checked against the account's own flags. It was previously
    ignored entirely, which meant a salesperson could pick "Stores", sign in,
    and land somewhere that looked nothing like the tab they chose — confusing,
    even though the flags still governed access. Now the mismatch is named.

    This is NOT the access control: `role_required` on every view remains that,
    and the chip cannot grant anything. It only refuses a sign-in that would
    otherwise be silently misleading.
    """
    if request.user.is_authenticated:
        return redirect(reverse(landing_url_name(request.user)))

    selected = (request.POST.get('role') or 'sales').strip()

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, 'Incorrect username or password.')
        elif not user.is_active:
            messages.error(request, 'That account has been disabled.')
        else:
            flag, label = LOGIN_ROLE_FLAGS.get(selected, (None, ''))
            holds_it = flag is not None and getattr(user, flag, False)
            # Superusers pass any chip — Admin inspects every department.
            if not holds_it and not user.is_superuser:
                mine = role_label(user)
                should = _chip_for(user)
                _f, should_label = LOGIN_ROLE_FLAGS.get(should, (None, ''))
                if should_label:
                    messages.error(
                        request,
                        f'You signed in under “{label or selected}”, but this '
                        f'account is {mine}. Select “{should_label}” and try again.',
                    )
                    # Move the chip for them, so the retry is one click.
                    selected = should
                else:
                    messages.error(
                        request,
                        f'This account has no department role assigned, so it '
                        f'cannot sign in under “{label or selected}”. Ask an '
                        f'administrator to add a role.',
                    )
            else:
                login(request, user)
                if not request.POST.get('keep_signed_in'):
                    request.session.set_expiry(0)
                return redirect(reverse(landing_url_name(user)))

    return render(request, 'users/login.html', {
        # Keeps the chosen chip lit after a failed attempt.
        'selected_role': selected,
    })


def logout_view(request):
    logout(request)
    return redirect('users:login')
