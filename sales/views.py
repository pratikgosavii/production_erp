from django.shortcuts import render


def dashboard(request):
    """Sales Dashboard / My Quotations — static UI preview."""
    return render(request, 'sales/dashboard.html')


def quotation_create(request):
    """Quotation form — static UI preview."""
    return render(request, 'sales/quotation_form.html')


def manager_approvals(request):
    """Sales Manager — Quotation Approval Queue — static UI preview."""
    return render(request, 'sales/manager_approvals.html')
