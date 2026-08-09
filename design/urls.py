from django.urls import path
from . import views

app_name = 'design'

urlpatterns = [
    path('queue/', views.queue, name='queue'),
    path('job/<str:so_no>/', views.job_detail, name='job_detail'),
]
