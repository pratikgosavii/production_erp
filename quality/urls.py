from django.urls import path

from . import views

app_name = 'quality'

urlpatterns = [
    path('ncr/', views.register, name='register'),
    path('ncr/raise/', views.raise_manual, name='raise_manual'),
    path('ncr/<int:pk>/capa/', views.save_capa, name='save_capa'),
    path('ncr/<int:pk>/close/', views.close, name='close'),
]
