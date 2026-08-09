from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('quotation/new/', views.quotation_create, name='quotation_create'),
    path('manager/approvals/', views.manager_approvals, name='manager_approvals'),
]
