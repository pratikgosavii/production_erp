from django.shortcuts import render


def queue(request):
    """Design Queue — static UI preview."""
    return render(request, 'design/queue.html')


def job_detail(request, so_no):
    """Design Job Detail — static UI preview."""
    return render(request, 'design/job_detail.html', {'so_no': so_no})
