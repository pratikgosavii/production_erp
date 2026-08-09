from django.urls import path

from . import views

app_name = 'masters'

urlpatterns = [
    path('item-codes/', views.item_codes, name='item_codes'),
    path('item-codes/save/', views.item_code_save, name='item_code_save'),
    path('item-codes/revise/', views.item_code_revise, name='item_code_revise'),
    path('item-codes/<int:pk>/toggle/', views.item_code_toggle, name='item_code_toggle'),
    path('customers/', views.customers, name='customers'),
    path('customers/save/', views.customer_save, name='customer_save'),
]
