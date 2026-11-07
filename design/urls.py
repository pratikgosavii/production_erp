from django.urls import path

from . import views

app_name = 'design'

urlpatterns = [
    path('queue/', views.queue, name='queue'),

    # One screen per ORDER; ?item=<pk> selects which item code is open, and
    # every POST below carries the same pk back so the redirect lands on the
    # item that was acted on.
    path('job/<str:so_no>/', views.job_detail, name='job_detail'),

    # State changes: POST + redirect, one route each (§9 audit trail). Each
    # one acts on a single SOItem, never on the whole order.
    path('job/<str:so_no>/start/', views.start_design, name='start_design'),
    path('job/<str:so_no>/spec/', views.save_spec, name='save_spec'),
    path('job/<str:so_no>/revision/', views.upload_revision, name='upload_revision'),
    path('job/<str:so_no>/reserve/', views.reserve_sheet, name='reserve_sheet'),
    path('job/<str:so_no>/proof/', views.send_proof, name='send_proof'),
    path('job/<str:so_no>/query/', views.raise_query, name='raise_query'),
    path('job/<str:so_no>/query/<int:query_id>/reply/', views.reply_query, name='reply_query'),
    path('job/<str:so_no>/query/<int:query_id>/close/', views.close_query, name='close_query'),
    path('job/<str:so_no>/approve/', views.approve_design, name='approve_design'),
]
