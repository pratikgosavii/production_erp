from django.urls import path

from . import views

app_name = 'production'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('job-card/<str:jc_no>/', views.job_card, name='job_card'),

    # State changes: POST + redirect.
    path('ready/priority/', views.set_priority, name='set_priority'),
    path('ready/release/', views.send_to_production, name='send_to_production'),
    path('ready/job-card/', views.create_job_card, name='create_job_card'),
    path('job-card/<str:jc_no>/start/', views.start_work, name='start_work'),
    path('job-card/<str:jc_no>/stations/', views.confirm_stations, name='confirm_stations'),
    path('job-card/<str:jc_no>/complete/', views.complete_station, name='complete_station'),
]
