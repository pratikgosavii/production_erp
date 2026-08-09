from django.shortcuts import render


def dashboard(request):
    """Stores — Raw Material Inventory — static UI preview."""
    return render(request, 'stores/dashboard.html')


def log_sheet(request):
    """Stores — Log New Sheet (blank QR batch / full details) — static UI preview."""
    return render(request, 'stores/log_sheet.html')


def qr_labels(request):
    """Stores — Generated QR labels, print / download sheet — static UI preview."""
    return render(request, 'stores/qr_labels.html')


def scan(request):
    """Stores — Scan a sheet QR label (camera + manual fallback) — static UI preview."""
    return render(request, 'stores/scan.html')


def sheet_detail(request, uid):
    """Stores — Sheet detail form opened after a scan — static UI preview."""
    return render(request, 'stores/sheet_detail.html', {'uid': uid})
