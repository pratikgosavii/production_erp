from django.urls import path
from . import views

app_name = 'production'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('job-card/<str:jc_no>/', views.job_card, name='job_card'),
]
