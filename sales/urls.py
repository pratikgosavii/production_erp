from django.urls import path

from . import views

app_name = 'sales'

urlpatterns = [
    # Salesperson
    path('dashboard/', views.dashboard, name='dashboard'),
    path('quotation/new/', views.quotation_create, name='quotation_create'),
    path('quotation/<int:pk>/edit/', views.quotation_edit, name='quotation_edit'),
    path('quotation/<int:pk>/send/', views.quotation_send, name='quotation_send'),
    path('quotation/<int:pk>/revise/', views.quotation_revise, name='quotation_revise'),
    path('quotation/<int:pk>/po/', views.quotation_po, name='quotation_po'),
    path('quotation/<int:pk>/convert/', views.quotation_convert, name='quotation_convert'),

    # Sales Manager
    path('manager/approvals/', views.manager_approvals, name='manager_approvals'),
    path('manager/approvals/<int:pk>/approve/', views.quotation_approve, name='quotation_approve'),
    path('manager/approvals/<int:pk>/reject/', views.quotation_reject, name='quotation_reject'),
    path('manager/approvals/<int:pk>/revision/', views.quotation_request_revision,
         name='quotation_request_revision'),
]
