from django.shortcuts import render


def dashboard(request):
    """Production Dashboard — Ready Queue — static UI preview."""
    return render(request, 'production/dashboard.html')


def job_card(request, jc_no):
    """Job Card — Station Flow — static UI preview."""
    return render(request, 'production/job_card.html', {'jc_no': jc_no})
