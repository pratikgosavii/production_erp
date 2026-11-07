"""
URL configuration for production_erp project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.shortcuts import redirect

urlpatterns = [
    path('', lambda req: redirect('users:login'), name='home'),
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('users/', include('users.urls')),
    path('sales/', include('sales.urls')),
    path('accounts/', include('accounts.urls')),
    path('design/', include('design.urls')),
    path('production/', include('production.urls')),
    path('stores/', include('stores.urls')),
    path('masters/', include('masters.urls')),
    path('quality/', include('quality.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Every role_required refusal lands here (see core.views.permission_denied).
handler403 = 'core.views.permission_denied'
