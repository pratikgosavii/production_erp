from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('order-booking/', views.order_booking, name='order_booking'),

    # Read-only full detail of one booked Sales Order, addressed by its
    # business number (SO-1076), not its pk — the number is what people quote.
    path('order/<str:so_no>/', views.order_detail, name='order_detail'),

    # Acting on a quotation that has not been booked yet (no SO exists).
    path('order-booking/quotation/<int:pk>/book/', views.book_order, name='book_order'),
    path('order-booking/quotation/<int:pk>/reject-po/', views.reject_po, name='reject_po'),
    path('order-booking/quotation/<int:pk>/request-drawing/',
         views.request_drawing, name='request_drawing'),

    # Acting on a booked Sales Order.
    path('order-booking/order/<int:pk>/hold/', views.hold_order, name='hold_order'),
    path('order-booking/order/<int:pk>/resume/', views.resume_order, name='resume_order'),
    path('order-booking/order/<int:pk>/cancel/', views.cancel_order, name='cancel_order'),
]
