from django.shortcuts import render, redirect


def login_view(request):
    """Login page — static UI for now, backend auth wired later."""
    if request.method == 'POST':
        return render(request, 'users/login.html', {
            'error': 'Backend auth not wired yet — UI preview only.'
        })
    return render(request, 'users/login.html')


def logout_view(request):
    """Logout — redirects to login."""
    return redirect('users:login')
