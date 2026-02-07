"""
Tests tagging rest api views
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, quote_plus, urlparse

import ddt  # type: ignore[import]
import rules
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from openedx_tagging import api
from openedx_tagging.import_export import api as import_export_api
from openedx_tagging.import_export.parsers import ParserFormat
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from openedx_tagging.models.system_defined import SystemDefinedTaxonomy
from openedx_tagging.rest_api.paginators import TagsPagination
from openedx_tagging.rules import can_change_object_tag_objectid, can_view_object_tag_objectid

from .test_models import TestTagTaxonomyMixin
from .utils import pretty_format_tags

User = get_user_model()

TAXONOMY_LIST_URL = "/tagging/rest_api/v1/taxonomies/"
TAXONOMY_DETAIL_URL = "/tagging/rest_api/v1/taxonomies/{pk}/"
TAXONOMY_EXPORT_URL = "/tagging/rest_api/v1/taxonomies/{pk}/export/"
TAXONOMY_TAGS_URL = "/tagging/rest_api/v1/taxonomies/{pk}/tags/"
TAXONOMY_TAGS_IMPORT_URL = "/tagging/rest_api/v1/taxonomies/{pk}/tags/import/"
TAXONOMY_TAGS_IMPORT_PLAN_URL = "/tagging/rest_api/v1/taxonomies/{pk}/tags/import/plan/"
TAXONOMY_CREATE_IMPORT_URL = "/tagging/rest_api/v1/taxonomies/import/"


OBJECT_TAGS_RETRIEVE_URL = "/tagging/rest_api/v1/object_tags/{object_id}/"
OBJECT_TAG_COUNTS_URL = "/tagging/rest_api/v1/object_tag_counts/{object_id_pattern}/"
OBJECT_TAGS_UPDATE_URL = "/tagging/rest_api/v1/object_tags/{object_id}/"

LANGUAGE_TAXONOMY_ID = -1


def check_taxonomy(
    data,
    *,
    taxonomy_id: int,
    name: str,
    description="",
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    system_defined=False,
    visible_to_authors=True,
    can_change_taxonomy=None,
    can_delete_taxonomy=None,
    can_tag_object=None,
    export_id=None,
):
    """
    Check taxonomy data
    """
    assert data["id"] == taxonomy_id
    assert data["name"] == name
    assert data["description"] == description
    assert data["enabled"] == enabled
    assert data["allow_multiple"] == allow_multiple
    assert data["allow_free_text"] == allow_free_text
    assert data["system_defined"] == system_defined
    assert data["visible_to_authors"] == visible_to_authors
    assert data["can_change_taxonomy"] == can_change_taxonomy
    assert data["can_delete_taxonomy"] == can_delete_taxonomy
    assert data["can_tag_object"] == can_tag_object
    assert data["export_id"] == export_id


class TestTaxonomyViewMixin(APITestCase):
    """
    Mixin for taxonomy views. Adds users.
    """

    def setUp(self):
        super().setUp()

        self.user = User.objects.create(
            username="user",
            email="user@example.com",
        )

        self.staff = User.objects.create(
            username="staff",
            email="staff@example.com",
            is_staff=True,
        )


@ddt.ddt
class TestTaxonomyViewSet(TestTaxonomyViewMixin):
    """
    Test taxonomy view set
    """

    @ddt.data(
        (None, status.HTTP_200_OK, 4),
        (1, status.HTTP_200_OK, 3),
        (0, status.HTTP_200_OK, 1),
        (True, status.HTTP_200_OK, 3),
        (False, status.HTTP_200_OK, 1),
        ("True", status.HTTP_200_OK, 3),
        ("False", status.HTTP_200_OK, 1),
        ("1", status.HTTP_200_OK, 3),
        ("0", status.HTTP_200_OK, 1),
        (2, status.HTTP_400_BAD_REQUEST, None),
        ("invalid", status.HTTP_400_BAD_REQUEST, None),
    )
    @ddt.unpack
    def test_list_taxonomy_queryparams(self, enabled, expected_status: int, expected_count: int | None):
        api.create_taxonomy(name="Taxonomy enabled 1", enabled=True)
        api.create_taxonomy(name="Taxonomy enabled 2", enabled=True)
        api.create_taxonomy(name="Taxonomy disabled", enabled=False)

        url = TAXONOMY_LIST_URL

        self.client.force_authenticate(user=self.staff)
        if enabled is not None:
            response = self.client.get(url, {"enabled": enabled})
        else:
            response = self.client.get(url)
        assert response.status_code == expected_status

        # If we were able to list the taxonomies, check that we got the expected number back
        # We take into account the Language Taxonomy that is created by the system in a migration
        if status.is_success(expected_status):
            assert len(response.data["results"]) == expected_count

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED, 0),
        ("user", status.HTTP_200_OK, 10),
        ("staff", status.HTTP_200_OK, 20, True),
    )
    @ddt.unpack
    def test_list_taxonomy(self, user_attr: str | None, expected_status: int, tags_count: int, is_admin=False):
        taxonomy = api.create_taxonomy(name="Taxonomy enabled 1", enabled=True)
        for i in range(tags_count):
            tag = Tag(
                taxonomy=taxonomy,
                value=f"Tag {i}",
            )
            tag.save()

        url = TAXONOMY_LIST_URL

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.get(url)
        assert response.status_code == expected_status

        # Check response
        if status.is_success(expected_status):
            assert response.data["results"] == [
                {
                    "id": -1,
                    "name": "Languages",
                    "description": "Languages that are enabled on this system.",
                    "enabled": True,
                    "allow_multiple": False,
                    "allow_free_text": False,
                    "system_defined": True,
                    "visible_to_authors": True,
                    "tags_count": 0,
                    # System taxonomy cannot be modified
                    "can_change_taxonomy": False,
                    "can_delete_taxonomy": False,
                    "can_tag_object": False,
                    "export_id": "languages-v1",
                },
                {
                    "id": taxonomy.id,
                    "name": "Taxonomy enabled 1",
                    "description": "",
                    "enabled": True,
                    "allow_multiple": True,
                    "allow_free_text": False,
                    "system_defined": False,
                    "visible_to_authors": True,
                    "tags_count": tags_count,
                    # Enabled taxonomy can be modified by taxonomy admins
                    "can_change_taxonomy": is_admin,
                    "can_delete_taxonomy": is_admin,
                    # can_tag_object is False because we default to not allowing users to tag arbitrary objects.
                    # But specific uses of this code (like content_tagging) will override this perm for their use cases.
                    "can_tag_object": False,
                    "export_id": "2-taxonomy-enabled-1",
                },
            ]
            assert response.data.get("can_add_taxonomy") == is_admin

    def test_list_taxonomy_pagination(self) -> None:
        url = TAXONOMY_LIST_URL
        api.create_taxonomy(name="T1", enabled=True)
        api.create_taxonomy(name="T2", enabled=True)
        api.create_taxonomy(name="T3", enabled=False)
        api.create_taxonomy(name="T4", enabled=False)
        api.create_taxonomy(name="T5", enabled=False)

        self.client.force_authenticate(user=self.staff)

        query_params = {"page_size": 2, "page": 2}
        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_200_OK

        self.assertEqual(set(t["name"] for t in response.data["results"]), set(("T2", "T3")))
        parsed_url = urlparse(response.data["next"])

        next_page = parse_qs(parsed_url.query).get("page", [""])[0]
        assert next_page == "3"

    @ddt.data(
        ('user', False),
        ('staff', True),
    )
    @ddt.unpack
    def test_list_taxonomy_empty(self, user_attr, expected_can_add) -> None:
        # Delete the language taxonomy so we can get an empty list
        language_taxonomy = api.get_taxonomy(LANGUAGE_TAXONOMY_ID)
        if language_taxonomy:
            language_taxonomy.delete()

        url = TAXONOMY_LIST_URL
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)
        response = self.client.get(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "count": 0,
            "current_page": 1,
            "next": None,
            "num_pages": 1,
            "previous": None,
            "start": 0,
            "results": [],
            "can_add_taxonomy": expected_can_add,
        }

    def test_list_taxonomy_query_count(self):
        """
        Test how many queries are used when retrieving taxonomies and permissions
        """
        api.create_taxonomy(name="T1", enabled=True)
        api.create_taxonomy(name="T2", enabled=True)

        url = TAXONOMY_LIST_URL

        self.client.force_authenticate(user=self.user)
        with self.assertNumQueries(3):
            response = self.client.get(url)

        assert response.status_code == 200
        assert not response.data["can_add_taxonomy"]
        assert len(response.data["results"]) == 3
        for taxonomy in response.data["results"]:
            assert not taxonomy["can_change_taxonomy"]
            assert not taxonomy["can_delete_taxonomy"]
            assert not taxonomy["can_tag_object"]

    def test_list_invalid_page(self) -> None:
        url = TAXONOMY_LIST_URL

        self.client.force_authenticate(user=self.user)

        query_params = {"page": 123123}

        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_language_taxonomy(self):
        """
        Test the "Language" taxonomy that's included.
        """
        self.client.force_authenticate(user=self.user)
        response = self.client.get(TAXONOMY_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        taxonomy_list = response.data["results"]
        assert len(taxonomy_list) == 1
        check_taxonomy(
            taxonomy_list[0],
            taxonomy_id=LANGUAGE_TAXONOMY_ID,
            name="Languages",
            description="Languages that are enabled on this system.",
            allow_multiple=False,  # We may change this in the future to allow multiple language tags
            system_defined=True,
            can_change_taxonomy=False,
            can_delete_taxonomy=False,
            can_tag_object=False,
            export_id='languages-v1',
        )

    @ddt.data(
        (None, {"enabled": True}, status.HTTP_401_UNAUTHORIZED),
        (None, {"enabled": False}, status.HTTP_401_UNAUTHORIZED),
        ("user", {"enabled": True}, status.HTTP_200_OK),
        ("user", {"enabled": False}, status.HTTP_404_NOT_FOUND),
        ("staff", {"enabled": True}, status.HTTP_200_OK, True),
        ("staff", {"enabled": False}, status.HTTP_200_OK, True),
    )
    @ddt.unpack
    def test_detail_taxonomy(
        self, user_attr: str | None, taxonomy_data: dict[str, bool], expected_status: int, is_admin=False,
    ):
        create_data = {
            "name": "taxonomy detail test",
            "export_id": "taxonomy_detail_test",
            **taxonomy_data
        }
        taxonomy = api.create_taxonomy(**create_data)  # type: ignore[arg-type]
        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.get(url)
        assert response.status_code == expected_status

        if status.is_success(expected_status):
            expected_data = create_data
            expected_data["can_change_taxonomy"] = is_admin
            expected_data["can_delete_taxonomy"] = is_admin
            expected_data["can_tag_object"] = False
            check_taxonomy(response.data, taxonomy_id=taxonomy.pk, **expected_data)  # type: ignore[arg-type]

    def test_detail_system_taxonomy(self):
        url = TAXONOMY_DETAIL_URL.format(pk=LANGUAGE_TAXONOMY_ID)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_detail_taxonomy_404(self) -> None:
        url = TAXONOMY_DETAIL_URL.format(pk=123123)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_detail_taxonomy_invalud_pk(self) -> None:
        url = TAXONOMY_DETAIL_URL.format(pk="invalid")

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_403_FORBIDDEN),
        ("staff", status.HTTP_201_CREATED),
    )
    @ddt.unpack
    def test_create_taxonomy(self, user_attr: str | None, expected_status: int):
        url = TAXONOMY_LIST_URL

        create_data = {
            "name": "taxonomy_data_2",
            "description": "This is a description",
            "enabled": False,
            "allow_multiple": True,
            "export_id": 'taxonomy_data_2',
        }

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.post(url, create_data, format="json")
        assert response.status_code == expected_status

        # If we were able to create the taxonomy, check if it was created
        if status.is_success(expected_status):
            create_data["can_change_taxonomy"] = True
            create_data["can_delete_taxonomy"] = True
            create_data["can_tag_object"] = False
            check_taxonomy(response.data, taxonomy_id=response.data["id"], **create_data)  # type: ignore[arg-type]
            url = TAXONOMY_DETAIL_URL.format(pk=response.data["id"])

            response = self.client.get(url)
            check_taxonomy(response.data, taxonomy_id=response.data["id"], **create_data)  # type: ignore[arg-type]

    def test_create_without_export_id(self):
        url = TAXONOMY_LIST_URL

        create_data = {
            "name": "Taxonomy Data 3",
            "description": "This is a description",
            "enabled": False,
            "allow_multiple": True,
        }

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(url, create_data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        create_data["can_change_taxonomy"] = True
        create_data["can_delete_taxonomy"] = True
        create_data["can_tag_object"] = False
        check_taxonomy(
            response.data,
            taxonomy_id=response.data["id"],
            export_id="2-taxonomy-data-3",
            **create_data,
        )

    @ddt.data(
        {},
        {"name": "Error taxonomy 1", "export_id": "Invalid value"},  # Invalid export_id
        {"name": "Error taxonomy 3", "export_id": "test", "enabled": "Invalid value"},
    )
    def test_create_taxonomy_error(self, create_data: dict[str, str]):
        url = TAXONOMY_LIST_URL

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(url, create_data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data({"name": "System defined taxonomy", "system_defined": True})
    def test_create_taxonomy_system_defined(self, create_data):
        """
        Cannont create a taxonomy with system_defined=true
        """
        url = TAXONOMY_LIST_URL

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(url, create_data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert not response.data["system_defined"]

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_403_FORBIDDEN),
        ("staff", status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_update_taxonomy(self, user_attr, expected_status):
        taxonomy = api.create_taxonomy(
            name="test update taxonomy",
            description="taxonomy description",
            enabled=True,
        )

        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.put(url, {"name": "new name"}, format="json")
        assert response.status_code == expected_status

        # If we were able to update the taxonomy, check if the name changed
        if status.is_success(expected_status):
            response = self.client.get(url)
            check_taxonomy(
                response.data,
                taxonomy_id=response.data["id"],
                **{
                    "name": "new name",
                    "description": "taxonomy description",
                    "enabled": True,
                    "can_change_taxonomy": True,
                    "can_delete_taxonomy": True,
                    "can_tag_object": False,
                    "export_id": "2-test-update-taxonomy",
                },
            )

    @ddt.data(
        (False, status.HTTP_200_OK),
        (True, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_update_taxonomy_system_defined(self, system_defined, expected_status):
        """
        Test that we can't update system_defined field
        """
        taxonomy = api.create_taxonomy(
            name="test system taxonomy",
            taxonomy_class=SystemDefinedTaxonomy if system_defined else None,
        )
        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(url, {"name": "new name"}, format="json")
        assert response.status_code == expected_status

    def test_update_taxonomy_404(self):
        url = TAXONOMY_DETAIL_URL.format(pk=123123)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(url, {"name": "new name"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_403_FORBIDDEN),
        ("staff", status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_patch_taxonomy(self, user_attr, expected_status):
        taxonomy = api.create_taxonomy(name="test patch taxonomy", enabled=False)

        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.patch(url, {"name": "new name", "enabled": True}, format="json")
        assert response.status_code == expected_status

        # If we were able to update the taxonomy, check if the name changed
        if status.is_success(expected_status):
            response = self.client.get(url)
            check_taxonomy(
                response.data,
                taxonomy_id=response.data["id"],
                **{
                    "name": "new name",
                    "enabled": True,
                    "can_change_taxonomy": True,
                    "can_delete_taxonomy": True,
                    "can_tag_object": False,
                    "export_id": '2-test-patch-taxonomy',
                },
            )

    @ddt.data(
        (False, status.HTTP_200_OK),
        (True, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_patch_taxonomy_system_defined(self, system_defined, expected_status):
        """
        Test that we can't patch system_defined field
        """
        taxonomy = api.create_taxonomy(
            name="test system taxonomy",
            taxonomy_class=SystemDefinedTaxonomy if system_defined else None,
        )
        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(url, {"name": "New name"}, format="json")
        assert response.status_code == expected_status

    def test_patch_taxonomy_404(self):
        url = TAXONOMY_DETAIL_URL.format(pk=123123)

        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(url, {"name": "new name"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_403_FORBIDDEN),
        ("staff", status.HTTP_204_NO_CONTENT),
    )
    @ddt.unpack
    def test_delete_taxonomy(self, user_attr, expected_status):
        taxonomy = api.create_taxonomy(name="test delete taxonomy")

        url = TAXONOMY_DETAIL_URL.format(pk=taxonomy.pk)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.delete(url)
        assert response.status_code == expected_status

        # If we were able to delete the taxonomy, check that it's really gone
        if status.is_success(expected_status):
            response = self.client.get(url)
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_taxonomy_404(self):
        url = TAXONOMY_DETAIL_URL.format(pk=123123)

        self.client.force_authenticate(user=self.staff)
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        ("csv", "text"),
        ("json", "application/json")
    )
    @ddt.unpack
    def test_export_taxonomy(self, output_format, content_type):
        """
        Tests if a user can export a taxonomy
        """
        taxonomy = api.create_taxonomy(name="T1")
        for i in range(20):
            # Valid ObjectTags
            Tag.objects.create(taxonomy=taxonomy, value=f"Tag {i}").save()

        url = TAXONOMY_EXPORT_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url, {"output_format": output_format})
        assert response.status_code == status.HTTP_200_OK
        if output_format == "json":
            expected_data = import_export_api.export_tags(taxonomy, ParserFormat.JSON)
        else:
            expected_data = import_export_api.export_tags(taxonomy, ParserFormat.CSV)

        assert response.headers['Content-Type'] == content_type
        assert response.content == expected_data.encode("utf-8")

    @ddt.data(
        ("csv", "text/csv"),
        ("json", "application/json")
    )
    @ddt.unpack
    def test_export_taxonomy_download(self, output_format, content_type):
        """
        Tests if a user can export a taxonomy with download option
        """
        taxonomy = api.create_taxonomy(name="T1")
        for i in range(20):
            api.add_tag_to_taxonomy(taxonomy=taxonomy, tag=f"Tag {i}")

        url = TAXONOMY_EXPORT_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url, {"output_format": output_format, "download": True})
        assert response.status_code == status.HTTP_200_OK
        if output_format == "json":
            expected_data = import_export_api.export_tags(taxonomy, ParserFormat.JSON)
        else:
            expected_data = import_export_api.export_tags(taxonomy, ParserFormat.CSV)

        assert response.headers['Content-Type'] == content_type
        assert response.headers['Content-Disposition'] == f'attachment; filename="{taxonomy.name}.{output_format}"'
        assert response.content == expected_data.encode("utf-8")

    def test_export_taxonomy_invalid_param_output_format(self):
        """
        Tests if a user can export a taxonomy using an invalid output_format param
        """
        taxonomy = api.create_taxonomy(name="T1")

        url = TAXONOMY_EXPORT_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url, {"output_format": "html", "download": True})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_taxonomy_invalid_param_download(self):
        """
        Tests if a user can export a taxonomy using an invalid output_format param
        """
        taxonomy = api.create_taxonomy(name="T1")

        url = TAXONOMY_EXPORT_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url, {"output_format": "json", "download": "invalid"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_taxonomy_unauthorized(self):
        """
        Tests if a user can export a taxonomy that he doesn't have authorization
        """
        # Only staff can view a disabled taxonomy
        taxonomy = api.create_taxonomy(name="T1", enabled=False)

        url = TAXONOMY_EXPORT_URL.format(pk=taxonomy.pk)

        self.client.force_authenticate(user=self.user)
        response = self.client.get(url, {"output_format": "json"})

        # Return 404, because the user doesn't have permission to view the taxonomy
        assert response.status_code == status.HTTP_404_NOT_FOUND


@ddt.ddt
class TestObjectTagViewSet(TestTagTaxonomyMixin, APITestCase):
    """
    Testing various cases for the ObjectTagView.
    """

    def setUp(self):
        super().setUp()

        def _change_object_permission(user, object_id: str) -> bool:
            """
            For testing, let everyone have edit object permission on object_id "abc" and "limit_tag_count"
            """
            basic_object_ids = ("abc", "limit_tag_count", "problem7", "problem15", "html7")
            complex_object_ids = ("abc.xyz", "limit_tag_count1.1", "problem7.2", "problem1.5", "html7.3")
            if object_id in basic_object_ids or object_id in complex_object_ids:
                return True

            return can_change_object_tag_objectid(user, object_id)

        def _view_object_permission(user, object_id: str) -> bool:
            """
            For testing, let everyone have view object permission on all objects but "unauthorized_id"
            """
            if object_id == "unauthorized_id":
                return False

            return can_view_object_tag_objectid(user, object_id)

        # Override the object permission for the test
        rules.set_perm("oel_tagging.change_objecttag_objectid", _change_object_permission)
        rules.set_perm("oel_tagging.view_objecttag_objectid", _view_object_permission)

        # Create a staff user:
        self.staff = User.objects.create(username="staff", email="staff@example.com", is_staff=True)

        # For this test, allow multiple "Life on Earth" tags:
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED, 'problem15'),
        ("user_1", status.HTTP_200_OK, 'problem1.5'),
        ("staff", status.HTTP_200_OK, 'problem1.5'),
    )
    @ddt.unpack
    def test_retrieve_object_tags(self, user_attr, expected_status, object_id):
        """
        Test retrieving object tags
        """

        # Apply the object tags that we're about to retrieve:
        api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=["Mammalia", "Fungi"])
        api.tag_object(object_id=object_id, taxonomy=self.user_taxonomy, tags=[self.user_1.username])

        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.get(url)
        assert response.status_code == expected_status

        if status.is_success(expected_status):
            # Check the response, first converting from OrderedDict to regular dicts for simplicity.
            assert response.data == {
                # In the future, this API may allow retrieving tags for multiple objects at once, so it's grouped by
                # object ID.
                object_id: {
                    "taxonomies": [
                        {
                            "name": "Life on Earth",
                            "taxonomy_id": 1,
                            "can_tag_object": True,
                            "tags": [
                                # Note: based on tree order (Animalia before Fungi), this tag comes first even though it
                                # starts with "M" and Fungi starts with "F"
                                {
                                    "value": "Mammalia",
                                    "lineage": ["Eukaryota", "Animalia", "Chordata", "Mammalia"],
                                    "can_delete_objecttag": False,
                                },
                                {
                                    "value": "Fungi",
                                    "lineage": ["Eukaryota", "Fungi"],
                                    "can_delete_objecttag": False,
                                },
                            ],
                            "export_id": "life_on_earth"
                        },
                        {
                            "name": "User Authors",
                            "taxonomy_id": 3,
                            "can_tag_object": True,
                            "tags": [
                                {
                                    "value": "test_user_1",
                                    "lineage": ["test_user_1"],
                                    "can_delete_objecttag": False,
                                },
                            ],
                            "export_id": "user_authors"
                        }
                    ],
                },
            }

    def prepare_for_sort_test(self, expected_perm=False) -> tuple[str, list[dict]]:
        """
        Tag an object with tags from the "sort test" taxonomy
        """
        object_id = "problem7.2"
        # Some selected tags to use, from the taxonomy create by self.create_sort_test_taxonomy()
        sort_test_tags = [
            "ANVIL",
            "Android",
            "azores islands",
            "abstract",
            "11111111",
            "111",
            "123",
            "1 A",
            "1111-grandchild",
        ]

        # Apply the object tags:
        taxonomy = self.create_sort_test_taxonomy()
        api.tag_object(object_id=object_id, taxonomy=taxonomy, tags=sort_test_tags)

        # Properties shared by all returned object tags
        shared_props = {
            "can_delete_objecttag": expected_perm,
        }

        # The result we expect to see when retrieving the object tags, after applying the list above.
        # Note: the full taxonomy looks like the following, so this is the order we
        # expect, although not all of these tags were included.
        # 1
        #   1 A
        #   11
        #   11111
        #     1111-grandchild
        #   2
        # 111
        #   11111111
        #   123
        # abstract
        #   Andes
        #   azores islands
        # ALPHABET
        #   aardvark
        #   abacus
        #   Android
        #   ANVIL
        #   azure
        sort_test_applied_result = [
            {"value": "1 A", "lineage": ["1", "1 A"], **shared_props},
            {"value": "1111-grandchild", "lineage": ["1", "11111", "1111-grandchild"], **shared_props},
            {"value": "111", "lineage": ["111"], **shared_props},
            {"value": "11111111", "lineage": ["111", "11111111"], **shared_props},
            {"value": "123", "lineage": ["111", "123"], **shared_props},
            {"value": "abstract", "lineage": ["abstract"], **shared_props},
            {"value": "azores islands", "lineage": ["abstract", "azores islands"], **shared_props},
            {"value": "Android", "lineage": ["ALPHABET", "Android"], **shared_props},
            {"value": "ANVIL", "lineage": ["ALPHABET", "ANVIL"], **shared_props},
        ]
        return object_id, sort_test_applied_result

    def test_retrieve_object_tags_sorted(self):
        """
        Test the sort order of the object tags retrieved from the get object
        tags API.
        """
        object_id, sort_test_applied_result = self.prepare_for_sort_test(expected_perm=False)

        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)
        self.client.force_authenticate(user=self.user_1)
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data[object_id]["taxonomies"][0]["name"] == "Sort Test"
        assert response.data[object_id]["taxonomies"][0]["tags"] == sort_test_applied_result

    def test_retrieve_object_tags_query_count(self):
        """
        Test how many queries are used when retrieving object tags and permissions
        """
        expected_perm = True
        expected_remove_perm = False
        object_id, sort_test_applied_result = self.prepare_for_sort_test(expected_remove_perm)
        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)

        self.client.force_authenticate(user=self.user_1)
        with self.assertNumQueries(1):
            response = self.client.get(url)

        assert response.status_code == 200
        assert len(response.data[object_id]["taxonomies"]) == 1
        assert response.data[object_id]["taxonomies"][0]["can_tag_object"] == expected_perm
        assert response.data[object_id]["taxonomies"][0]["tags"] == sort_test_applied_result

    def test_retrieve_object_tags_unauthorized(self):
        """
        Test retrieving object tags from an unauthorized object_id
        """
        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id="unauthorized_id")
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED, 'html7.3'),
        ("user_1", status.HTTP_200_OK, 'html7'),
        ("staff", status.HTTP_200_OK, 'html7.3'),
    )
    @ddt.unpack
    def test_retrieve_object_tags_taxonomy_queryparam(
        self, user_attr, expected_status, object_id
    ):
        """
        Test retrieving object tags for specific taxonomies provided
        """

        # Apply the object tags that we're about to retrieve:
        api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=["Mammalia", "Fungi"])
        api.tag_object(object_id=object_id, taxonomy=self.user_taxonomy, tags=[self.user_1.username])

        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.get(url, {"taxonomy": self.user_taxonomy.pk})
        assert response.status_code == expected_status
        if status.is_success(expected_status):
            assert response.data == {
                # In the future, this API may allow retrieving tags for multiple objects at once, so it's grouped by
                # object ID.
                object_id: {
                    "taxonomies": [
                        # The "Life on Earth" tags are excluded here...
                        {
                            "name": "User Authors",
                            "taxonomy_id": 3,
                            "can_tag_object": True,
                            "tags": [
                                {
                                    "value": "test_user_1",
                                    "lineage": ["test_user_1"],
                                    "can_delete_objecttag": False,
                                },
                            ],
                            "export_id": "user_authors",
                        }
                    ],
                },
            }

    @ddt.data(
        (None, "abc.xyz", status.HTTP_401_UNAUTHORIZED),
        ("user_1", "abc", status.HTTP_400_BAD_REQUEST),
        ("staff", "abc.xyz", status.HTTP_400_BAD_REQUEST),
    )
    @ddt.unpack
    def test_retrieve_object_tags_invalid_taxonomy_queryparam(self, user_attr, object_id, expected_status):
        """
        Test retrieving object tags for invalid taxonomy
        """
        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        # Invalid Taxonomy
        response = self.client.get(url, {"taxonomy": 123123})
        assert response.status_code == expected_status

    @ddt.data(
        (None, "POST", status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        (None, "PATCH", status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        (None, "DELETE", status.HTTP_401_UNAUTHORIZED, "abc"),
        ("user_1", "POST", status.HTTP_405_METHOD_NOT_ALLOWED, "abc"),
        ("user_1", "PATCH", status.HTTP_405_METHOD_NOT_ALLOWED, "abc"),
        ("user_1", "DELETE", status.HTTP_405_METHOD_NOT_ALLOWED, "abc.xyz"),
        ("staff", "POST", status.HTTP_405_METHOD_NOT_ALLOWED, "abc.xyz"),
        ("staff", "PATCH", status.HTTP_405_METHOD_NOT_ALLOWED, "abc.xyz"),
        ("staff", "DELETE", status.HTTP_405_METHOD_NOT_ALLOWED, "abc"),
    )
    @ddt.unpack
    def test_object_tags_remaining_http_methods(
        self,
        user_attr,
        http_method,
        expected_status,
        object_id
    ):
        """
        Test POST/PATCH/DELETE method for ObjectTagView

        Only staff users should have permissions to perform the actions,
        however the methods are currently not allowed.
        """
        url = OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        if http_method == "POST":
            response = self.client.post(url, {"test": "payload"}, format="json")
        elif http_method == "PATCH":
            response = self.client.patch(url, {"test": "payload"}, format="json")
        else:
            assert http_method == "DELETE"
            response = self.client.delete(url)

        assert response.status_code == expected_status

    @ddt.data(
        # Users and staff can add tags
        (None, "language_taxonomy", {}, ["Portuguese"], status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        ("user_1", "language_taxonomy", {}, ["Portuguese"], status.HTTP_200_OK, "abc"),
        ("staff", "language_taxonomy", {}, ["Portuguese"], status.HTTP_200_OK, "abc.xyz"),
        # user_1s and staff can clear add tags
        (None, "taxonomy", {}, ["Fungi"], status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        ("user_1", "taxonomy", {}, ["Fungi"], status.HTTP_200_OK, "abc.xyz"),
        ("staff", "taxonomy", {}, ["Fungi"], status.HTTP_200_OK, "abc.xyz"),
        # Nobody can add tag using a disabled taxonomy
        (None, "taxonomy", {"enabled": False}, ["Fungi"], status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        ("user_1", "taxonomy", {"enabled": False}, ["Fungi"], status.HTTP_403_FORBIDDEN, "abc"),
        ("staff", "taxonomy", {"enabled": False}, ["Fungi"], status.HTTP_403_FORBIDDEN, "abc.xyz"),
        # If allow_multiple=True, multiple tags can be added, but not if it's false:
        ("user_1", "taxonomy", {"allow_multiple": True}, ["Mammalia", "Fungi"], status.HTTP_200_OK, "abc.xyz"),
        (
            "user_1", "taxonomy", {"allow_multiple": False},
            ["Mammalia", "Fungi"], status.HTTP_400_BAD_REQUEST, "abc.xyz"
        ),
        # user_1s and staff can add tags using an open taxonomy
        (None, "free_text_taxonomy", {}, ["tag1"], status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        ("user_1", "free_text_taxonomy", {}, ["tag1", "tag2"], status.HTTP_200_OK, "abc"),
        ("staff", "free_text_taxonomy", {}, ["tag1", "tag4"], status.HTTP_200_OK, "abc.xyz"),
        # Nobody can add tags using a disabled open taxonomy
        (None, "free_text_taxonomy", {"enabled": False}, ["tag1"], status.HTTP_401_UNAUTHORIZED, "abc.xyz"),
        ("user_1", "free_text_taxonomy", {"enabled": False}, ["tag1"], status.HTTP_403_FORBIDDEN, "abc.xyz"),
        ("staff", "free_text_taxonomy", {"enabled": False}, ["tag1"], status.HTTP_403_FORBIDDEN, "abc"),
        # Can't add invalid/nonexistent tags using a closed taxonomy
        (None, "language_taxonomy", {}, ["Invalid"], status.HTTP_401_UNAUTHORIZED, "abc"),
        ("user_1", "language_taxonomy", {}, ["Invalid"], status.HTTP_400_BAD_REQUEST, "abc.xyz"),
        ("staff", "language_taxonomy", {}, ["Invalid"], status.HTTP_400_BAD_REQUEST, "abc"),
        ("staff", "taxonomy", {}, ["Invalid"], status.HTTP_400_BAD_REQUEST, "abc.xyz"),
    )
    @ddt.unpack
    def test_tag_object(  # pylint: disable=too-many-positional-arguments
        self,
        user_attr,
        taxonomy_attr,
        taxonomy_flags,
        tag_values,
        expected_status,
        object_id,
    ):
        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)
        if taxonomy_flags:
            for (k, v) in taxonomy_flags.items():
                setattr(taxonomy, k, v)
            taxonomy.save()

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)

        data = [{
            "taxonomy": taxonomy.pk,
            "tags": tag_values,
        }]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == expected_status
        if status.is_success(expected_status):
            assert [t["value"] for t in response.data[object_id]["taxonomies"][0]["tags"]] == tag_values
            # And retrieving the object tags again should return an identical response:
            assert response.data == self.client.get(OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)).data

    @ddt.data(
        ("abc",),
        ("abc.xyz",),
    )
    @ddt.unpack
    def test_tag_object_multiple_taxonomy(self, object_id):
        self.client.force_authenticate(user=self.staff)

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
        tag_value_1 = ["Tag 4"]
        tag_value_2 = ["Mammalia", "Fungi"]
        data = [
            {
                "taxonomy": self.import_taxonomy.pk,
                "tags": tag_value_1,
            },
            {
                "taxonomy": self.taxonomy.pk,
                "tags": tag_value_2,
            },
        ]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert [t["value"] for t in response.data[object_id]["taxonomies"][0]["tags"]] == tag_value_1
        assert [t["value"] for t in response.data[object_id]["taxonomies"][1]["tags"]] == tag_value_2
        # And retrieving the object tags again should return an identical response:
        assert response.data == self.client.get(OBJECT_TAGS_RETRIEVE_URL.format(object_id=object_id)).data

    @ddt.data(
        # Users and staff can clear tags
        (None, {}, status.HTTP_401_UNAUTHORIZED),
        ("user_1", {}, status.HTTP_200_OK),
        ("staff", {}, status.HTTP_200_OK),
        # Nobody can clear tags using a disabled taxonomy
        (None, {"enabled": False}, status.HTTP_401_UNAUTHORIZED),
        ("user_1", {"enabled": False}, status.HTTP_403_FORBIDDEN),
        ("staff", {"enabled": False}, status.HTTP_403_FORBIDDEN),
        # ... and it doesn't matter if it's free text or closed:
        ("staff", {"enabled": False, "allow_free_text": False}, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_tag_object_clear(self, user_attr, taxonomy_flags, expected_status):
        """
        Test that authorized users can *remove* tags using this API
        """
        object_id = "abc"

        # First create an object tag:
        api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=["Fungi"])

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        if taxonomy_flags:
            for (k, v) in taxonomy_flags.items():
                setattr(self.taxonomy, k, v)
            self.taxonomy.save()

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)

        data = [{
            "taxonomy": self.taxonomy.pk,
            "tags": [],
        }]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == expected_status
        if status.is_success(expected_status):
            # Now there are no tags applied:
            assert response.data[object_id]["taxonomies"] == []
        else:
            # Make sure the object tags are unchanged:
            if not self.taxonomy.enabled:  # The taxonomy has to be enabled for us to see the tags though:
                self.taxonomy.enabled = True
                self.taxonomy.save()
            assert [ot.value for ot in api.get_object_tags(object_id=object_id)] == ["Fungi"]

    def test_tag_object_clear_multiple_taxonomy(self):
        object_id = "abc"
        self.client.force_authenticate(user=self.staff)
        api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=["Mammalia", "Fungi"])
        api.tag_object(object_id=object_id, taxonomy=self.import_taxonomy, tags=["Tag 4"])

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
        data = [
            {
                "taxonomy": self.import_taxonomy.pk,
                "tags": [],
            },
            {
                "taxonomy": self.taxonomy.pk,
                "tags": [],
            },
        ]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data[object_id]["taxonomies"] == []

    def test_tag_object_clear_simple_taxonomy(self):
        object_id = "abc"
        self.client.force_authenticate(user=self.staff)
        tag_values = ["Mammalia", "Fungi"]
        api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=tag_values)
        api.tag_object(object_id=object_id, taxonomy=self.import_taxonomy, tags=["Tag 4"])

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
        data = [
            {
                "taxonomy": self.import_taxonomy.pk,
                "tags": [],
            },
        ]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert [t["value"] for t in response.data[object_id]["taxonomies"][0]["tags"]] == tag_values

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user_1", status.HTTP_403_FORBIDDEN),
        ("staff", status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_tag_object_without_permission(self, user_attr, expected_status):
        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        url = OBJECT_TAGS_UPDATE_URL.format(object_id="view_only")

        data = [{
            "taxonomy": self.taxonomy.pk,
            "tags": ["Tag 1"],
        }]

        response = self.client.put(url, {"tagsData": data}, format="json")
        assert response.status_code == expected_status
        assert not status.is_success(expected_status)  # No success cases here

    def test_tag_object_count_limit(self):
        """
        Checks if the limit of 100 tags per object is enforced
        """
        object_id = "limit_tag_count"
        dummy_taxonomies = self.create_100_taxonomies()

        url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(url, {"tagsData": [{
            "taxonomy": self.taxonomy.pk,
            "tags": ["Tag 1"],
        }]}, format="json")
        # Can't add another tag because the object already has 100 tags
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # The user can edit the tags that are already on the object
        for taxonomy in dummy_taxonomies:
            url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
            response = self.client.put(url, {"tagsData": [{
                "taxonomy": taxonomy.pk,
                "tags": ["New Tag"],
            }]}, format="json")
            assert response.status_code == status.HTTP_200_OK

        # Editing tags adding another one will fail
        for taxonomy in dummy_taxonomies:
            url = OBJECT_TAGS_UPDATE_URL.format(object_id=object_id)
            response = self.client.put(url, {"tagsData": [{
                "taxonomy": taxonomy.pk,
                "tags": ["New Tag 1", "New Tag 2"],
            }]}, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestObjectTagCountsViewSet(TestTagTaxonomyMixin, APITestCase):
    """
    Testing various cases for counting how many tags are applied to several objects.
    """

    def test_get_counts(self):
        """
        Test retrieving the counts of tags applied to various content objects.

        This API does NOT bother doing any permission checks as the "# of tags" is not considered sensitive information.
        """
        # Course 2
        api.tag_object(object_id="course02-unit01-problem01", taxonomy=self.free_text_taxonomy, tags=["other"])
        # Course 7 Unit 1
        api.tag_object(object_id="course07-unit01-problem01.1", taxonomy=self.free_text_taxonomy, tags=["a", "b", "c"])
        api.tag_object(object_id="course07-unit01-problem02.2", taxonomy=self.free_text_taxonomy, tags=["a", "b"])
        # Course 7 Unit 2
        api.tag_object(object_id="course07-unit02-problem01", taxonomy=self.free_text_taxonomy, tags=["b"])
        api.tag_object(object_id="course07-unit02-problem02.2", taxonomy=self.free_text_taxonomy, tags=["c", "d"])
        api.tag_object(object_id="course07-unit02-problem03", taxonomy=self.free_text_taxonomy, tags=["N", "M"])
        api.tag_object(object_id="course07-unit02-problem03.3", taxonomy=self.free_text_taxonomy, tags=["N", "M"])
        api.tag_object(object_id="course07-unit02-problem03", taxonomy=self.taxonomy, tags=["Mammalia"])

        def check(object_id_pattern: str, count_implicit=False):
            url = OBJECT_TAG_COUNTS_URL.format(object_id_pattern=object_id_pattern)
            if count_implicit:
                url += "?count_implicit"
            result = self.client.get(url)
            assert result.status_code == status.HTTP_200_OK
            return result.data

        with self.assertNumQueries(1):
            assert check(object_id_pattern="course02-*") == {
                "course02-unit01-problem01": 1,
            }
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit01-*") == {
                "course07-unit01-problem01.1": 3,
                "course07-unit01-problem02.2": 2,
            }
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit02-*") == {
                "course07-unit02-problem01": 1,
                "course07-unit02-problem02.2": 2,
                "course07-unit02-problem03": 3,
                "course07-unit02-problem03.3": 2,
            }
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit02-*", count_implicit=True) == {
                "course07-unit02-problem01": 1,
                "course07-unit02-problem02.2": 2,
                "course07-unit02-problem03.3": 2,
                # "Mammalia" includes 1 explicit + 3 implicit tags: "Eukaryota > Animalia > Chordata > Mammalia"
                # so problem03 has 2 free text tags and "4" life on earth tags:
                "course07-unit02-problem03": 6,
            }
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit*") == {
                "course07-unit01-problem01.1": 3,
                "course07-unit01-problem02.2": 2,
                "course07-unit02-problem01": 1,
                "course07-unit02-problem02.2": 2,
                "course07-unit02-problem03": 3,
                "course07-unit02-problem03.3": 2,
            }
        # Can also use a comma to separate explicit object IDs:
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit01-problem01.1") == {
                "course07-unit01-problem01.1": 3,
            }
        with self.assertNumQueries(1):
            assert check(object_id_pattern="course07-unit01-problem01.1,course07-unit02-problem02.2") == {
                "course07-unit01-problem01.1": 3,
                "course07-unit02-problem02.2": 2,
            }

    def test_get_counts_invalid_spec(self):
        """
        Test handling of an invalid object tag pattern
        """
        result = self.client.get(OBJECT_TAG_COUNTS_URL.format(object_id_pattern="abc*def"))
        assert result.status_code == status.HTTP_400_BAD_REQUEST
        assert "Wildcard matches are only supported if the * is at the end." in str(result.content)


class TestTaxonomyTagsView(TestTaxonomyViewMixin):
    """
    Tests the list/create/update/delete tags of taxonomy view
    """

    fixtures = ["tests/openedx_tagging/fixtures/tagging.yaml"]

    def setUp(self):
        self.small_taxonomy = Taxonomy.objects.get(name="Life on Earth")
        self.large_taxonomy = Taxonomy(name="Large Taxonomy")
        self.large_taxonomy.save()

        self.small_taxonomy_url = TAXONOMY_TAGS_URL.format(pk=self.small_taxonomy.pk)
        self.large_taxonomy_url = TAXONOMY_TAGS_URL.format(pk=self.large_taxonomy.pk)

        self.root_tags_count = 51
        self.children_tags_count = [12, 12]  # 51 * 12 * 12 = 7344 tags

        self.page_size = TagsPagination().page_size

        return super().setUp()

    def _create_tag(self, depth: int, parent: Tag | None = None):
        """
        Creates tags and children in a recursive way.
        """
        tag_count = self.large_taxonomy.tag_set.count()
        tag = Tag(
            taxonomy=self.large_taxonomy,
            parent=parent,
            value=f"Tag {tag_count}",
        )
        tag.save()
        if depth < len(self.children_tags_count):
            for _ in range(self.children_tags_count[depth]):
                self._create_tag(depth + 1, parent=tag)
        return tag

    def _build_large_taxonomy(self):
        # Pupulates the large taxonomy with tags
        for _ in range(self.root_tags_count):
            self._create_tag(0)

    def test_invalid_taxonomy(self):
        url = TAXONOMY_TAGS_URL.format(pk=212121)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_authorized_user(self):
        # Not authenticated user
        response = self.client.get(self.small_taxonomy_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        self.small_taxonomy.enabled = False
        self.small_taxonomy.save()
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.small_taxonomy_url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_small_taxonomy_root(self):
        """
        Test explicitly requesting only the root tags of a small taxonomy.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.small_taxonomy_url + "?include_counts")
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data.get("results", [])

        # Count of root tags
        root_count = self.small_taxonomy.tag_set.filter(parent=None).count()
        assert len(results) == root_count

        # Checking tag fields on the first tag returned:
        root_tag = self.small_taxonomy.tag_set.get(id=results[0].get("_id"))
        assert results[0].get("value") == root_tag.value
        assert results[0].get("child_count") == root_tag.children.count()
        assert results[0].get("depth") == 0  # root tags always have depth 0
        assert results[0].get("parent_value") is None
        assert results[0].get("usage_count") == 0

        # Check that we can load sub-tags of that tag:
        sub_tags_response = self.client.get(results[0]["sub_tags_url"])
        assert sub_tags_response.status_code == status.HTTP_200_OK
        sub_tags_result = sub_tags_response.data["results"]
        assert len(sub_tags_result) == root_tag.children.count()
        assert set(t["value"] for t in sub_tags_result) == set(t.value for t in root_tag.children.all())

        # Checking pagination values
        assert data.get("next") is None
        assert data.get("previous") is None
        assert data.get("count") == root_count
        assert data.get("num_pages") == 1
        assert data.get("current_page") == 1
        assert data.get("can_add_tag")

    def test_small_taxonomy(self):
        """
        Test loading all the tags of a small taxonomy at once.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.small_taxonomy_url + "?full_depth_threshold=1000")
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data.get("results", [])
        assert pretty_format_tags(results) == [
            "Archaea (None) (children: 3)",
            "  DPANN (Archaea) (children: 0)",
            "  Euryarchaeida (Archaea) (children: 0)",
            "  Proteoarchaeota (Archaea) (children: 0)",
            "Bacteria (None) (children: 2)",
            "  Archaebacteria (Bacteria) (children: 0)",
            "  Eubacteria (Bacteria) (children: 0)",
            "Eukaryota (None) (children: 5 + 8)",
            "  Animalia (Eukaryota) (children: 7 + 1)",
            "    Arthropoda (Animalia) (children: 0)",
            "    Chordata (Animalia) (children: 1)",
            "    Cnidaria (Animalia) (children: 0)",
            "    Ctenophora (Animalia) (children: 0)",
            "    Gastrotrich (Animalia) (children: 0)",
            "    Placozoa (Animalia) (children: 0)",
            "    Porifera (Animalia) (children: 0)",
            "  Fungi (Eukaryota) (children: 0)",
            "  Monera (Eukaryota) (children: 0)",
            "  Plantae (Eukaryota) (children: 0)",
            "  Protista (Eukaryota) (children: 0)",
        ]

        # Checking pagination values
        assert data.get("next") is None
        assert data.get("previous") is None
        assert data.get("count") == len(results)
        assert data.get("num_pages") == 1
        assert data.get("current_page") == 1

    def test_small_taxonomy_paged(self):
        """
        Test loading only the first few of the tags of a small taxonomy.
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.small_taxonomy_url + "?page_size=2")
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        # When pagination is active, we only load a single "layer" at a time:
        assert pretty_format_tags(data["results"]) == [
            "Archaea (None) (children: 3)",
            "Bacteria (None) (children: 2)",
        ]

        # Checking pagination values
        assert data.get("next") is not None
        assert data.get("previous") is None
        assert data.get("count") == 3
        assert data.get("num_pages") == 2
        assert data.get("current_page") == 1

        # Get the next page:
        next_response = self.client.get(data.get("next"))
        assert next_response.status_code == status.HTTP_200_OK
        next_data = next_response.data
        assert pretty_format_tags(next_data["results"]) == [
            "Eukaryota (None) (children: 5 + 8)",
        ]
        assert next_data.get("current_page") == 2

    def test_small_search(self):
        """
        Test performing a search
        """
        search_term = 'eU'
        url = f"{self.small_taxonomy_url}?search_term={search_term}&full_depth_threshold=100"
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        assert pretty_format_tags(data["results"], parent=False) == [
            "Archaea (children: 1)",  # No match in this tag, but a child matches so it's included
            "  Euryarchaeida (children: 0)",
            "Bacteria (children: 1)",  # No match in this tag, but a child matches so it's included
            "  Eubacteria (children: 0)",
            "Eukaryota (children: 0)",
        ]

        # Checking pagination values
        assert data.get("next") is None
        assert data.get("previous") is None
        assert data.get("count") == 5
        assert data.get("num_pages") == 1
        assert data.get("current_page") == 1

    def test_small_search_shallow(self):
        """
        Test performing a search without full_depth_threshold
        """
        search_term = 'eU'
        url = f"{self.small_taxonomy_url}?search_term={search_term}"
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        assert pretty_format_tags(data["results"], parent=False) == [
            "Archaea (children: 1)",  # No match in this tag, but a child matches so it's included
            "Bacteria (children: 1)",  # No match in this tag, but a child matches so it's included
            "Eukaryota (children: 0)",
        ]

        # Checking pagination values
        assert data.get("next") is None
        assert data.get("previous") is None
        assert data.get("count") == 3
        assert data.get("num_pages") == 1
        assert data.get("current_page") == 1

        # And we can load the sub_tags_url to "drill down" into the search:
        sub_tags_response = self.client.get(data["results"][0]["sub_tags_url"])
        assert sub_tags_response.status_code == status.HTTP_200_OK

        assert pretty_format_tags(sub_tags_response.data["results"], parent=False) == [
            # This tag maches our search results and is a child of the previously returned Archaea tag:
            "  Euryarchaeida (children: 0)",
        ]

    def test_small_query_count(self):
        """
        Test how many queries are used when retrieving small taxonomies+tags and permissions
        """
        url = f"{self.small_taxonomy_url}?search_term=eU"

        self.client.force_authenticate(user=self.staff)
        with self.assertNumQueries(4):
            response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_add_tag"]
        assert len(response.data["results"]) == 3
        for taxonomy in response.data["results"]:
            assert taxonomy["can_change_tag"]
            assert taxonomy["can_delete_tag"]

    def test_empty_results(self):
        """
        Test that various queries return an empty list
        """
        self.client.force_authenticate(user=self.staff)

        def assert_empty(url):
            response = self.client.get(url)
            assert response.status_code == status.HTTP_200_OK
            assert response.data["results"] == []
            assert response.data["count"] == 0

        # Search terms that won't match any tags:
        assert_empty(f"{self.small_taxonomy_url}?search_term=foobar")
        assert_empty(f"{self.small_taxonomy_url}?search_term=foobar&full_depth_threshold=1000")
        # Requesting children of leaf tags is always an empty result.
        # Prior versions of the code would sometimes throw an exception when trying to handle these.
        assert_empty(f"{self.small_taxonomy_url}?parent_tag=Fungi")
        assert_empty(f"{self.small_taxonomy_url}?parent_tag=Fungi&full_depth_threshold=1000")
        assert_empty(f"{self.small_taxonomy_url}?search_term=eu&parent_tag=Euryarchaeida")
        assert_empty(f"{self.small_taxonomy_url}?search_term=eu&parent_tag=Euryarchaeida&full_depth_threshold=1000")

    def test_large_taxonomy(self):
        """
        Test listing the tags in a large taxonomy (~7,000 tags).
        """
        self._build_large_taxonomy()
        self.client.force_authenticate(user=self.staff)

        url = self.large_taxonomy_url + "?include_counts"
        with self.assertNumQueries(3):
            response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data["results"]

        assert pretty_format_tags(results) == [
            "Tag 0 (None) (used: 0, children: 12 + 144)",
            "Tag 1099 (None) (used: 0, children: 12 + 144)",
            "Tag 1256 (None) (used: 0, children: 12 + 144)",
            "Tag 1413 (None) (used: 0, children: 12 + 144)",
            "Tag 157 (None) (used: 0, children: 12 + 144)",
            "Tag 1570 (None) (used: 0, children: 12 + 144)",
            "Tag 1727 (None) (used: 0, children: 12 + 144)",
            "Tag 1884 (None) (used: 0, children: 12 + 144)",
            "Tag 2041 (None) (used: 0, children: 12 + 144)",
            "Tag 2198 (None) (used: 0, children: 12 + 144)",
            # ... there are 41 more root tags but they're excluded from this first result page.
        ]

        # Count of paginated root tags
        assert len(results) == self.page_size

        # Checking some other tag fields not covered by the pretty-formatted string above:
        root_tag = self.large_taxonomy.tag_set.get(value=results[0].get("value"))
        assert results[0].get("_id") == root_tag.id
        assert results[0].get("sub_tags_url") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}"
            f"/tags/?parent_tag={quote_plus(results[0]['value'])}"
        )
        assert results[0].get("can_change_tag")
        assert results[0].get("can_delete_tag")

        # Checking pagination values
        assert data.get("next") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}/tags/?include_counts=&page=2"
        )
        assert data.get("previous") is None
        assert data.get("count") == self.root_tags_count
        assert data.get("num_pages") == 6
        assert data.get("current_page") == 1
        assert data.get("can_add_tag")

    def test_next_page_large_taxonomy(self):
        self._build_large_taxonomy()
        self.client.force_authenticate(user=self.staff)

        # Gets the root tags to obtain the next links
        response = self.client.get(self.large_taxonomy_url)

        # Gets the next root tags
        response = self.client.get(response.data.get("next"))
        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # Checking pagination values
        assert data.get("next") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}/tags/?page=3"
        )
        assert data.get("previous") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}/tags/"
        )
        assert data.get("count") == self.root_tags_count
        assert data.get("num_pages") == 6
        assert data.get("current_page") == 2

    def test_large_search(self):
        """
        Test searching in a large taxonomy
        """
        self._build_large_taxonomy()
        self.client.force_authenticate(user=self.staff)

        # Perform the search with full_depth_threshold=1000, which will give us the full tree of results, since
        # there are less than 1000 matches:
        search_term = '11'
        response = self.client.get(f"{self.large_taxonomy_url}?search_term={search_term}&full_depth_threshold=1000")
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data["results"]
        assert pretty_format_tags(results)[:20] == [
            "Tag 0 (None) (children: 3 + 10)",  # First 2 results don't match but have children that match
            # Note the count here ---^ is not the total number of matching descendants, just the number of children
            # once we filter the tree to include only matches and their ancestors.
            "  Tag 1 (Tag 0) (children: 1)",
            "    Tag 11 (Tag 1) (children: 0)",
            "  Tag 105 (Tag 0) (children: 8)",  # Non-match but children match
            "    Tag 110 (Tag 105) (children: 0)",
            "    Tag 111 (Tag 105) (children: 0)",
            "    Tag 112 (Tag 105) (children: 0)",
            "    Tag 113 (Tag 105) (children: 0)",
            "    Tag 114 (Tag 105) (children: 0)",
            "    Tag 115 (Tag 105) (children: 0)",
            "    Tag 116 (Tag 105) (children: 0)",
            "    Tag 117 (Tag 105) (children: 0)",
            "  Tag 118 (Tag 0) (children: 1)",
            "    Tag 119 (Tag 118) (children: 0)",
            "Tag 1099 (None) (children: 9 + 93)",
            "  Tag 1100 (Tag 1099) (children: 12)",
            "    Tag 1101 (Tag 1100) (children: 0)",
            "    Tag 1102 (Tag 1100) (children: 0)",
            "    Tag 1103 (Tag 1100) (children: 0)",
            "    Tag 1104 (Tag 1100) (children: 0)",
        ]
        expected_num_results = 362
        assert data.get("count") == expected_num_results
        assert len(results) == expected_num_results
        assert data.get("num_pages") == 1
        assert data.get("current_page") == 1

        # Now, perform the search with full_depth_threshold=100, which will give us paginated results, since there are
        # more than 100 matches:
        response2 = self.client.get(f"{self.large_taxonomy_url}?search_term={search_term}&full_depth_threshold=100")
        assert response2.status_code == status.HTTP_200_OK

        data2 = response2.data
        results2 = data2["results"]
        assert pretty_format_tags(results2) == [
            # Notice that none of these root tags directly match the search query, but their children/grandchildren do
            "Tag 0 (None) (children: 3 + 10)",
            "Tag 1099 (None) (children: 9 + 93)",
            "Tag 1256 (None) (children: 2 + 2)",
            "Tag 1413 (None) (children: 1 + 1)",
            "Tag 157 (None) (children: 2 + 2)",
            "Tag 1570 (None) (children: 2 + 2)",
            "Tag 1727 (None) (children: 1 + 1)",
            "Tag 1884 (None) (children: 2 + 1)",
            "Tag 2041 (None) (children: 1 + 10)",
            "Tag 2198 (None) (children: 2 + 2)",
        ]
        assert data2.get("count") == 51
        assert data2.get("num_pages") == 6
        assert data2.get("current_page") == 1

        # Now load the results that are in the subtree of the root tag 'Tag 0'
        tag_0_subtags_url = results2[0]["sub_tags_url"]
        assert "full_depth_threshold=100" in tag_0_subtags_url
        response3 = self.client.get(tag_0_subtags_url)
        data3 = response3.data
        # Now the number of results is below our threshold (100), so the subtree gets returned as a single page:
        assert pretty_format_tags(data3["results"]) == [
            "  Tag 1 (Tag 0) (children: 1)",  # Non-match but children match
            "    Tag 11 (Tag 1) (children: 0)",  # Matches '11'
            "  Tag 105 (Tag 0) (children: 8)",  # Non-match but children match
            "    Tag 110 (Tag 105) (children: 0)",  # Matches '11'
            "    Tag 111 (Tag 105) (children: 0)",
            "    Tag 112 (Tag 105) (children: 0)",
            "    Tag 113 (Tag 105) (children: 0)",
            "    Tag 114 (Tag 105) (children: 0)",
            "    Tag 115 (Tag 105) (children: 0)",
            "    Tag 116 (Tag 105) (children: 0)",
            "    Tag 117 (Tag 105) (children: 0)",
            "  Tag 118 (Tag 0) (children: 1)",
            "    Tag 119 (Tag 118) (children: 0)",
        ]

    def test_get_children(self):
        self._build_large_taxonomy()
        self.client.force_authenticate(user=self.staff)

        # Get root tags to obtain the children link of a tag.
        response = self.client.get(self.large_taxonomy_url)
        results = response.data.get("results", [])

        # Get children tags
        response = self.client.get(results[0].get("sub_tags_url"))
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data.get("results", [])

        # Count of paginated children tags
        assert len(results) == self.page_size

        # Checking tag fields
        tag = self.large_taxonomy.tag_set.get(id=results[0].get("_id"))
        assert results[0].get("value") == tag.value
        assert results[0].get("parent_value") == tag.parent.value
        assert results[0].get("child_count") == tag.children.count()
        assert results[0].get("sub_tags_url") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}"
            f"/tags/?parent_tag={quote_plus(tag.value)}"
        )

        # Checking pagination values
        assert data.get("next") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}"
            f"/tags/?page=2&parent_tag={quote_plus(tag.parent.value)}"
        )
        assert data.get("previous") is None
        assert data.get("count") == self.children_tags_count[0]
        assert data.get("num_pages") == 2
        assert data.get("current_page") == 1

    def test_get_leaves(self):
        """
        Test getting the tags at depth=2, using "full_depth_threshold=1000" to
        load the whole subtree.
        """
        # Get tags at depth=2
        self.client.force_authenticate(user=self.staff)
        parent_tag = Tag.objects.get(value="Animalia")

        # Build url to get tags depth=2
        url = f"{self.small_taxonomy_url}?parent_tag={parent_tag.value}&full_depth_threshold=1000"
        response = self.client.get(url)
        results = response.data["results"]

        # Because the result is small, the result includes the complete tree below this one.
        assert pretty_format_tags(results) == [
            "    Arthropoda (Animalia) (children: 0)",
            "    Chordata (Animalia) (children: 1)",
            "      Mammalia (Chordata) (children: 0)",
            "    Cnidaria (Animalia) (children: 0)",
            "    Ctenophora (Animalia) (children: 0)",
            "    Gastrotrich (Animalia) (children: 0)",
            "    Placozoa (Animalia) (children: 0)",
            "    Porifera (Animalia) (children: 0)",
        ]
        assert response.data.get("next") is None

    def test_get_leaves_paginated(self):
        """
        Test getting depth=2 entries, disabling the feature to return the whole
        subtree if the result is small enough.
        """
        # Get tags at depth=2
        self.client.force_authenticate(user=self.staff)
        parent_tag = Tag.objects.get(value="Animalia")

        # Build url to get tags depth=2
        url = f"{self.small_taxonomy_url}?parent_tag={parent_tag.value}&page_size=5"
        response = self.client.get(url)
        results = response.data["results"]

        # Because the result is small, the result includes the complete tree below this one.
        assert pretty_format_tags(results) == [
            "    Arthropoda (Animalia) (children: 0)",
            "    Chordata (Animalia) (children: 1)",  # Note the child is not included
            "    Cnidaria (Animalia) (children: 0)",
            "    Ctenophora (Animalia) (children: 0)",
            "    Gastrotrich (Animalia) (children: 0)",
        ]
        next_url = response.data.get("next")
        assert next_url is not None
        response2 = self.client.get(next_url)
        results2 = response2.data["results"]
        assert pretty_format_tags(results2) == [
            "    Placozoa (Animalia) (children: 0)",
            "    Porifera (Animalia) (children: 0)",
        ]

    def test_next_children(self):
        self._build_large_taxonomy()
        self.client.force_authenticate(user=self.staff)

        # Get roots to obtain children link of a tag
        response = self.client.get(self.large_taxonomy_url)
        results = response.data.get("results", [])

        # Get the URL that gives us the children of the first root tag
        first_root_tag = results[0]
        response = self.client.get(first_root_tag.get("sub_tags_url"))

        # Get next page of children
        response = self.client.get(response.data.get("next"))
        assert response.status_code == status.HTTP_200_OK

        data = response.data
        results = data["results"]
        assert pretty_format_tags(results) == [
            # There are 12 child tags total, so on this second page, we see only 2 (10 were on the first page):
            "  Tag 79 (Tag 0) (children: 12)",
            "  Tag 92 (Tag 0) (children: 12)",
        ]

        # Checking pagination values
        assert data.get("next") is None
        assert data.get("previous") == (
            "http://testserver/tagging/"
            f"rest_api/v1/taxonomies/{self.large_taxonomy.id}/tags/?parent_tag={quote_plus(first_root_tag['value'])}"
        )
        assert data.get("count") == self.children_tags_count[0]
        assert data.get("num_pages") == 2
        assert data.get("current_page") == 2

    def test_create_tag_in_taxonomy_while_loggedout(self):
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_tag_in_taxonomy_without_permission(self):
        self.client.force_authenticate(user=self.user)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_tag_in_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED

        data = response.data

        self.assertIsNotNone(data.get("_id"))
        self.assertEqual(data.get("value"), new_tag_value)
        self.assertIsNone(data.get("parent_value"))
        self.assertIsNone(data.get("external_id"))
        self.assertIsNone(data.get("sub_tags_link"))
        self.assertEqual(data.get("child_count"), 0)

    def test_create_tag_in_taxonomy_with_parent(self):
        self.client.force_authenticate(user=self.staff)
        parent_tag = self.small_taxonomy.tag_set.filter(parent=None).first()
        new_tag_value = "New Child Tag"
        new_external_id = "extId"

        create_data = {
            "tag": new_tag_value,
            "parent_tag_value": parent_tag.value,
            "external_id": new_external_id
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED

        data = response.data

        self.assertIsNotNone(data.get("_id"))
        self.assertEqual(data.get("value"), new_tag_value)
        self.assertEqual(data.get("parent_value"), parent_tag.value)
        self.assertEqual(data.get("external_id"), new_external_id)
        self.assertIsNone(data.get("sub_tags_link"))
        self.assertEqual(data.get("child_count"), 0)

    def test_create_tag_in_invalid_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        invalid_taxonomy_url = TAXONOMY_TAGS_URL.format(pk=919191)
        response = self.client.post(
            invalid_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_tag_in_free_text_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        # Setting free text flag on taxonomy
        self.small_taxonomy.allow_free_text = True
        self.small_taxonomy.save()

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_tag_in_system_defined_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        # Setting taxonomy to be system defined
        self.small_taxonomy.taxonomy_class = SystemDefinedTaxonomy
        self.small_taxonomy.save()

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_tag_in_taxonomy_with_invalid_parent_tag(self):
        self.client.force_authenticate(user=self.staff)
        invalid_parent_tag = "Invalid Tag"
        new_tag_value = "New Child Tag"

        create_data = {
            "tag": new_tag_value,
            "parent_tag_value": invalid_parent_tag,
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_tag_in_taxonomy_with_parent_tag_in_other_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        tag_in_other_taxonomy = Tag.objects.get(id=1)
        new_tag_value = "New Child Tag"

        create_data = {
            "tag": new_tag_value,
            "parent_tag_value": tag_in_other_taxonomy.value,
        }

        response = self.client.post(
            self.large_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_tag_in_taxonomy_with_already_existing_value(self):
        self.client.force_authenticate(user=self.staff)
        new_tag_value = "New Tag"

        create_data = {
            "tag": new_tag_value
        }

        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED

        # Make request again with the same Tag value after it was created
        response = self.client.post(
            self.small_taxonomy_url, create_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_tag_in_taxonomy_while_loggedout(self):
        updated_tag_value = "Updated Tag"

        # Existing Tag that will be updated
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        update_data = {
            "tag": existing_tag.value,
            "updated_tag_value": updated_tag_value
        }

        # Test updating using the PUT method
        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_tag_in_taxonomy_without_permission(self):
        self.client.force_authenticate(user=self.user)
        updated_tag_value = "Updated Tag"

        # Existing Tag that will be updated
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        update_data = {
            "tag": existing_tag.value,
            "updated_tag_value": updated_tag_value
        }

        # Test updating using the PUT method
        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_tag_in_taxonomy_with_different_methods(self):
        self.client.force_authenticate(user=self.staff)
        updated_tag_value = "Updated Tag"
        updated_tag_value_2 = "Updated Tag 2"

        # Existing Tag that will be updated
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        update_data = {
            "tag": existing_tag.value,
            "updated_tag_value": updated_tag_value
        }

        # Test updating using the PUT method
        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # Check that Tag value got updated
        self.assertEqual(data.get("_id"), existing_tag.id)
        self.assertEqual(data.get("value"), updated_tag_value)
        self.assertEqual(data.get("parent_value"), existing_tag.parent)
        self.assertEqual(data.get("external_id"), existing_tag.external_id)

        # Test updating using the PATCH method
        update_data["tag"] = updated_tag_value  # Since the value changed
        update_data["updated_tag_value"] = updated_tag_value_2
        response = self.client.patch(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # Check the Tag value got updated again
        self.assertEqual(data.get("_id"), existing_tag.id)
        self.assertEqual(data.get("value"), updated_tag_value_2)
        self.assertEqual(data.get("parent_value"), existing_tag.parent)
        self.assertEqual(data.get("external_id"), existing_tag.external_id)

    def test_update_tag_in_taxonomy_reflects_changes_in_object_tags(self):
        self.client.force_authenticate(user=self.staff)

        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        # Setup ObjectTags
        # _value=existing_tag.value
        object_tag_1 = ObjectTag.objects.create(
            object_id="abc", taxonomy=self.small_taxonomy, tag=existing_tag
        )
        object_tag_2 = ObjectTag.objects.create(
            object_id="def", taxonomy=self.small_taxonomy, tag=existing_tag
        )
        object_tag_3 = ObjectTag.objects.create(
            object_id="ghi", taxonomy=self.small_taxonomy, tag=existing_tag
        )

        assert object_tag_1.value == existing_tag.value
        assert object_tag_2.value == existing_tag.value
        assert object_tag_3.value == existing_tag.value

        updated_tag_value = "Updated Tag"
        update_data = {
            "tag": existing_tag.value,
            "updated_tag_value": updated_tag_value
        }

        # Test updating using the PUT method
        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # Check that Tag value got updated
        self.assertEqual(data.get("_id"), existing_tag.id)
        self.assertEqual(data.get("value"), updated_tag_value)
        self.assertEqual(data.get("parent_value"), None)
        self.assertEqual(data.get("external_id"), existing_tag.external_id)

        # Check that the ObjectTags got updated as well
        object_tag_1.refresh_from_db()
        self.assertEqual(object_tag_1.value, updated_tag_value)
        object_tag_2.refresh_from_db()
        self.assertEqual(object_tag_2.value, updated_tag_value)
        object_tag_3.refresh_from_db()
        self.assertEqual(object_tag_3.value, updated_tag_value)

    def test_update_tag_in_taxonomy_with_invalid_tag(self):
        self.client.force_authenticate(user=self.staff)
        updated_tag_value = "Updated Tag"

        update_data = {
            "tag": 919191,
            "updated_tag_value": updated_tag_value
        }

        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_tag_in_taxonomy_with_tag_in_other_taxonomy(self):
        self.client.force_authenticate(user=self.staff)
        updated_tag_value = "Updated Tag"
        tag_in_other_taxonomy = Tag.objects.get(id=1)

        update_data = {
            "tag": tag_in_other_taxonomy.value,
            "updated_tag_value": updated_tag_value
        }

        response = self.client.put(
            self.large_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_tag_in_taxonomy_with_no_tag_value_provided(self):
        self.client.force_authenticate(user=self.staff)

        # Existing Tag that will be updated
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        update_data = {
            "tag": existing_tag.value
        }

        response = self.client.put(
            self.small_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_tag_in_invalid_taxonomy(self):
        self.client.force_authenticate(user=self.staff)

        # Existing Tag that will be updated
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        updated_tag_value = "Updated Tag"
        update_data = {
            "tag": existing_tag.value,
            "updated_tag_value": updated_tag_value
        }

        invalid_taxonomy_url = TAXONOMY_TAGS_URL.format(pk=919191)
        response = self.client.put(
            invalid_taxonomy_url, update_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_single_tag_from_taxonomy_while_loggedout(self):
        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [existing_tag.value],
            "with_subtags": True
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_single_tag_from_taxonomy_without_permission(self):
        self.client.force_authenticate(user=self.user)
        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [existing_tag.value],
            "with_subtags": True
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_single_tag_from_taxonomy(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [existing_tag.value],
            "with_subtags": True
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        # Check that Tag no longer exists
        with self.assertRaises(Tag.DoesNotExist):
            existing_tag.refresh_from_db()

    def test_delete_multiple_tags_from_taxonomy(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tags that will be deleted
        existing_tags = self.small_taxonomy.tag_set.filter(parent=None)[:3]

        delete_data = {
            "tags": [existing_tag.value for existing_tag in existing_tags],
            "with_subtags": True
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        # Check that Tags no longer exists
        for existing_tag in existing_tags:
            with self.assertRaises(Tag.DoesNotExist):
                existing_tag.refresh_from_db()

    def test_delete_tag_with_subtags_should_fail_without_flag_passed(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [existing_tag.value]
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_tag_in_invalid_taxonomy(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [existing_tag.value]
        }

        invalid_taxonomy_url = TAXONOMY_TAGS_URL.format(pk=919191)
        response = self.client.delete(
            invalid_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_tag_in_taxonomy_with_invalid_tag(self):
        self.client.force_authenticate(user=self.staff)

        delete_data = {
            "tags": ["Invalid Tag"]
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_tag_with_tag_in_other_taxonomy(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tag in other Taxonomy
        tag_in_other_taxonomy = self.small_taxonomy.tag_set.filter(parent=None).first()

        delete_data = {
            "tags": [tag_in_other_taxonomy.value]
        }

        response = self.client.delete(
            self.large_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_tag_in_taxonomy_without_subtags(self):
        self.client.force_authenticate(user=self.staff)

        # Get Tag that will be deleted
        existing_tag = self.small_taxonomy.tag_set.filter(children__isnull=True).first()

        delete_data = {
            "tags": [existing_tag.value]
        }

        response = self.client.delete(
            self.small_taxonomy_url, delete_data, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        # Check that Tag no longer exists
        with self.assertRaises(Tag.DoesNotExist):
            existing_tag.refresh_from_db()


class ImportTaxonomyMixin(TestTaxonomyViewMixin):
    """
    Mixin to test importing taxonomies.
    """
    def _get_file(self, tags: list, file_format: str) -> SimpleUploadedFile:
        """
        Returns a file for the given format.
        """
        if file_format == "csv":
            csv_data = "id,value"
            for tag in tags:
                csv_data += f"\n{tag['id']},{tag['value']}"
            return SimpleUploadedFile("taxonomy.csv", csv_data.encode(), content_type="text/csv")
        else:  # json
            json_data = {"tags": tags}
            return SimpleUploadedFile("taxonomy.json", json.dumps(json_data).encode(), content_type="application/json")


@ddt.ddt
class TestCreateImportView(ImportTaxonomyMixin, APITestCase):
    """
    Tests the create/import taxonomy action.
    """
    @ddt.data(
        "csv",
        "json",
    )
    def test_import(self, file_format: str) -> None:
        """
        Tests importing a valid taxonomy file.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy_export_id",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Check if the taxonomy was created
        taxonomy = response.data
        assert taxonomy["name"] == "Imported Taxonomy name"
        assert taxonomy["description"] == "Imported Taxonomy description"
        assert taxonomy["export_id"] == "imported_taxonomy_export_id"

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=taxonomy["id"])
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]

    def test_import_no_file(self) -> None:
        """
        Tests importing a taxonomy without a file.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy_description",
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "No file was submitted."

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_no_name(self, file_format) -> None:
        """
        Tests importing a taxonomy without specifing a name.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["taxonomy_name"][0] == "This field is required."

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    def test_import_invalid_format(self) -> None:
        """
        Tests importing a taxonomy with an invalid file format.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile("taxonomy.invalid", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy_description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "File type not supported: invalid"

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_invalid_content(self, file_format) -> None:
        """
        Tests importing a taxonomy with an invalid file content.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy_export_id",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Invalid '.{file_format}' format:" in response.data

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    def test_import_no_perm(self) -> None:
        """
        Tests importing a taxonomy using a user without permission.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, "json")

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_no_export_id(self, file_format) -> None:
        """
        Tests importing a taxonomy without providing an export ID, it should generate one
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)
        taxonomy_next_count = Taxonomy.objects.count() + 1

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                # "taxonomy_export_id": "imported_taxonomy_export_id",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Check if the taxonomy was created
        taxonomy = response.data
        assert taxonomy["name"] == "Imported Taxonomy name"
        assert taxonomy["description"] == "Imported Taxonomy description"
        assert taxonomy["export_id"] == f"{taxonomy_next_count}-imported-taxonomy-name"

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=taxonomy["id"])
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]


@ddt.ddt
class TestImportTagsView(ImportTaxonomyMixin, APITestCase):
    """
    Tests the taxonomy import tags action.
    """
    def _check_taxonomy_not_changed(self) -> None:
        """
        Checks if the self.taxonomy have the original tags.
        """
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(self.old_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == self.old_tags[i].value

    def setUp(self):
        ImportTaxonomyMixin.setUp(self)

        self.taxonomy = api.create_taxonomy(name="Test import taxonomy")
        tag_1 = Tag.objects.create(
            taxonomy=self.taxonomy,
            external_id="old_tag_1",
            value="Old tag 1",
        )
        tag_2 = Tag.objects.create(
            taxonomy=self.taxonomy,
            external_id="old_tag_2",
            value="Old tag 2",
        )
        self.old_tags = [tag_1, tag_2]

    @ddt.data(
        "csv",
        "json",
    )
    def test_import(self, file_format: str) -> None:
        """
        Tests importing a valid taxonomy file.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_200_OK

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_plan(self, file_format: str) -> None:
        """
        Tests planning import a valid taxonomy file.
        """
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is None
        assert response.data["task"]["status"] == "success"
        expected_plan = "Import plan for Test import taxonomy\n" \
            + "--------------------------------\n" \
            + "#1: Delete tag <TagItem> (old_tag_1 / Old tag 1)\n" \
            + "#2: Delete tag <TagItem> (old_tag_2 / Old tag 2)\n" \
            + "#3: Create a new tag with values (external_id=tag_1, value=Tag 1, parent_id=None).\n" \
            + "#4: Create a new tag with values (external_id=tag_2, value=Tag 2, parent_id=None).\n" \
            + "#5: Create a new tag with values (external_id=tag_3, value=Tag 3, parent_id=None).\n" \
            + "#6: Create a new tag with values (external_id=tag_4, value=Tag 4, parent_id=None).\n"
        assert response.data["plan"] == expected_plan

        self._check_taxonomy_not_changed()

    def test_import_no_file(self) -> None:
        """
        Tests importing a taxonomy without a file.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "No file was submitted."

        self._check_taxonomy_not_changed()

    def test_import_plan_no_file(self) -> None:
        """
        Tests planning import a taxonomy without a file.
        """
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "No file was submitted."

        self._check_taxonomy_not_changed()

    def test_import_invalid_format(self) -> None:
        """
        Tests importing a taxonomy with an invalid file format.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile("taxonomy.invalid", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "File type not supported: invalid"

        self._check_taxonomy_not_changed()

    def test_import_plan_invalid_format(self) -> None:
        """
        Tests planning import a taxonomy with an invalid file format.
        """
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile("taxonomy.invalid", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "File type not supported: invalid"

        self._check_taxonomy_not_changed()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_invalid_content(self, file_format) -> None:
        """
        Tests importing a taxonomy with an invalid file content.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Invalid '.{file_format}' format:" in response.data

        self._check_taxonomy_not_changed()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_plan_invalid_content(self, file_format) -> None:
        """
        Tests planning import a taxonomy with an invalid file content.
        """
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Invalid '.{file_format}' format:" in response.data["error"]

        self._check_taxonomy_not_changed()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_free_text(self, file_format) -> None:
        """
        Tests that importing tags into a free text taxonomy is not allowed.
        """
        self.taxonomy.allow_free_text = True
        self.taxonomy.save()
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == f"Invalid taxonomy ({self.taxonomy.id}): You cannot import a free-form taxonomy."

        # Check if the taxonomy was no tags, since it is free text
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == 0

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_plan_free_text(self, file_format) -> None:
        """
        Tests that planning import tags into a free text taxonomy is not allowed.
        """
        self.taxonomy.allow_free_text = True
        self.taxonomy.save()
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        expected_message = f"Invalid taxonomy ({self.taxonomy.id}): You cannot import a free-form taxonomy."
        assert response.data["error"] == expected_message

        # Check if the taxonomy was no tags, since it is free text
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == 0

    def test_import_no_perm(self) -> None:
        """
        Tests importing a taxonomy using a user without permission.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, "json")

        self.client.force_authenticate(user=self.user)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        self._check_taxonomy_not_changed()

    def test_import_plan_no_perm(self) -> None:
        """
        Tests planning import a taxonomy using a user without permission.
        """
        url = TAXONOMY_TAGS_IMPORT_PLAN_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, "json")

        self.client.force_authenticate(user=self.user)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        self._check_taxonomy_not_changed()
