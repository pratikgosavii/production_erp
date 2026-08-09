from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('order-booking/', views.order_booking, name='order_booking'),
]
