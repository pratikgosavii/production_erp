from django.urls import path

from . import views

app_name = 'stores'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('log-sheet/', views.log_sheet, name='log_sheet'),
    path('qr-labels/', views.qr_labels, name='qr_labels'),
    path('scan/', views.scan, name='scan'),
    path('scan/open/', views.scan_open, name='scan_open'),
    path('sheet/<str:uid>/', views.sheet_detail, name='sheet_detail'),
    path('sheet/<str:uid>/issue/', views.issue_sheet, name='issue_sheet'),
    path('sheet/<str:uid>/log-cut/', views.log_cut, name='log_cut'),
    path('dispatch/<int:pk>/dispatched/', views.mark_dispatched, name='mark_dispatched'),
]
