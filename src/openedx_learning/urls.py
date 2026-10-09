"""
Learning API URLs.

Maps each applet onto its own URL segment. A consuming project includes this module and never an
applet package directly, so ``applets/`` stays the internal layout detail that api.py and
models.py already exist to hide.

No ``app_name`` here on purpose: each applet declares its own, so route names stay unique per
applet rather than sharing one flat namespace.
"""

from django.urls import include, path

from .applets.cbe.rest_api import urls as cbe_urls

urlpatterns = [path("cbe/", include(cbe_urls))]
