from django.urls import path
from . import views

app_name = 'stores'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('log-sheet/', views.log_sheet, name='log_sheet'),
    path('qr-labels/', views.qr_labels, name='qr_labels'),
    path('scan/', views.scan, name='scan'),
    path('sheet/<str:uid>/', views.sheet_detail, name='sheet_detail'),
]
