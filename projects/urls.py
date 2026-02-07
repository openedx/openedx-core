from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from django.conf.urls.static import static

urlpatterns = [
    path("admin/doc/", include("django.contrib.admindocs.urls")),
    path("admin/", admin.site.urls),
    path("tagging/rest_api/", include("openedx_tagging.urls")),
    path('__debug__/', include('debug_toolbar.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
