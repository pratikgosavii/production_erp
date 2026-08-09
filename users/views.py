from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import reverse

from core.decorators import landing_url_name


def login_view(request):
    """Username + password only — OTP was dropped (§3.1).

    The role tabs on the form are a hint, not a credential: the landing page
    comes from the user's own flags, so picking the wrong tab cannot grant
    access to another department's screens.
    """
    if request.user.is_authenticated:
        return redirect(reverse(landing_url_name(request.user)))

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, 'Incorrect username or password.')
        elif not user.is_active:
            messages.error(request, 'That account has been disabled.')
        else:
            login(request, user)
            if not request.POST.get('keep_signed_in'):
                request.session.set_expiry(0)
            return redirect(reverse(landing_url_name(user)))

    return render(request, 'users/login.html')


def logout_view(request):
    logout(request)
    return redirect('users:login')
