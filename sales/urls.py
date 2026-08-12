from django.urls import path

from . import views

app_name = 'sales'

urlpatterns = [
    # Salesperson
    path('dashboard/', views.dashboard, name='dashboard'),
    path('quotation/new/', views.quotation_create, name='quotation_create'),
    # Read-only full detail — Sales, Sales Manager and Accounts all land here.
    path('quotation/<int:pk>/', views.quotation_detail, name='quotation_detail'),
    path('quotation/<int:pk>/edit/', views.quotation_edit, name='quotation_edit'),
    # The printable quotation. Inline by default, ?download=1 to save it.
    path('quotation/<int:pk>/pdf/', views.quotation_pdf, name='quotation_pdf'),
    path('quotation/<int:pk>/revise/', views.quotation_revise, name='quotation_revise'),
    path('quotation/<int:pk>/po/', views.quotation_po, name='quotation_po'),
    path('quotation/<int:pk>/convert/', views.quotation_convert, name='quotation_convert'),

    # Design Queries — the salesperson's inbox (§7.1). Closing is Design's call,
    # so there is deliberately no close route here.
    path('queries/', views.design_queries, name='queries'),
    path('queries/<int:pk>/reply/', views.query_reply, name='query_reply'),

    # Sales Manager
    path('manager/approvals/', views.manager_approvals, name='manager_approvals'),
    path('manager/quotations/', views.manager_quotations, name='manager_quotations'),
    path('manager/approvals/<int:pk>/approve/', views.quotation_approve, name='quotation_approve'),
    path('manager/approvals/<int:pk>/reject/', views.quotation_reject, name='quotation_reject'),
    path('manager/approvals/<int:pk>/revision/', views.quotation_request_revision,
         name='quotation_request_revision'),
]
