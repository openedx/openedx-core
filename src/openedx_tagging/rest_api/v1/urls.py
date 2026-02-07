"""
Taxonomies API v1 URLs.
"""

from django.urls.conf import include, path
from rest_framework.routers import DefaultRouter

from . import views, views_import

router = DefaultRouter()
router.register("taxonomies", views.TaxonomyView, basename="taxonomy")
router.register("object_tags", views.ObjectTagView, basename="object_tag")
router.register("object_tag_counts", views.ObjectTagCountsView, basename="object_tag_counts")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "taxonomies/<str:pk>/tags/",
        views.TaxonomyTagsView.as_view(),
        name="taxonomy-tags",
    ),
    path(
        "import/template.<str:file_ext>",
        views_import.TemplateView.as_view(),
        name="taxonomy-import-template",
    ),
]
