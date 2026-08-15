from django.urls import path

from . import views

app_name = 'production'

urlpatterns = [
    # Production Manager — orders first, then one order's items, then the board.
    path('dashboard/', views.dashboard, name='dashboard'),
    path('order/<str:so_no>/', views.order_detail, name='order_detail'),
    path('priority/', views.priority_board, name='priority_board'),

    # Floor Manager — the queue is his landing screen; a card is one job.
    path('floor/', views.floor_queue, name='floor_queue'),
    path('job-card/<str:jc_no>/', views.job_card, name='job_card'),

    # State changes: POST + redirect, except save_priority's JSON arm.
    path('priority/save/', views.save_priority, name='save_priority'),
    # The pre-board priority form posted here; kept alive so any bookmark or
    # older caller still lands on the one place priority is written.
    path('ready/priority/', views.save_priority, name='set_priority'),
    path('ready/release/', views.send_to_production, name='send_to_production'),
    path('ready/job-card/', views.create_job_card, name='create_job_card'),
    # Edit is refused once work has started; delete stays available either way.
    path('job-card/<str:jc_no>/edit/', views.edit_job_card, name='edit_job_card'),
    path('job-card/<str:jc_no>/delete/', views.delete_job_card, name='delete_job_card'),
    path('job-card/<str:jc_no>/start/', views.start_work, name='start_work'),
    path('job-card/<str:jc_no>/stations/', views.confirm_stations, name='confirm_stations'),
    # Start and Complete are separate POSTs so the clock measures real run
    # time rather than the gap since the previous station finished.
    # The Production Manager writes the stage instructions; the floor reads them.
    path('job-card/<str:jc_no>/notes/', views.save_station_notes, name='save_station_notes'),
    path('job-card/<str:jc_no>/station/start/', views.start_station, name='start_station'),
    path('job-card/<str:jc_no>/complete/', views.complete_station, name='complete_station'),
]
