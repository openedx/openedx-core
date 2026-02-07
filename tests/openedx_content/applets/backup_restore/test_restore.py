"""Tests for the lp_load management command."""
import os
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from openedx_content.applets.backup_restore.zipper import LearningPackageUnzipper, generate_staged_lp_key
from openedx_content.applets.collections import api as collections_api
from openedx_content.applets.components import api as components_api
from openedx_content.applets.publishing import api as publishing_api
from test_utils.zip_file_utils import folder_to_inmemory_zip

User = get_user_model()


class RestoreTestCase(TestCase):
    """Base test case for restore tests."""

    def setUp(self):
        super().setUp()
        self.fixtures_folder = os.path.join(os.path.dirname(__file__), "fixtures/library_backup")
        self.zip_file = folder_to_inmemory_zip(self.fixtures_folder)
        self.lp_key = "lib:WGU:LIB_C001"
        self.user = User.objects.create_user(username='lp_user', password='12345')


class RestoreLearningPackageCommandTest(RestoreTestCase):
    """Tests for the lp_load management command."""

    @patch("openedx_content.applets.backup_restore.api.load_learning_package")
    def test_restore_command(self, mock_load_learning_package):
        # Mock load_learning_package to return our in-memory zip file
        restore_result = LearningPackageUnzipper(self.zip_file, user=self.user).load()
        mock_load_learning_package.return_value = restore_result

        out = StringIO()
        # You can pass any dummy path, since load_learning_package is mocked
        call_command("lp_load", "dummy.zip", "lp_user", stdout=out)

        lp = self.verify_lp(restore_result["lp_restored_data"]["key"])
        self.verify_containers(lp)
        self.verify_components(lp)
        self.verify_collections(lp)

    def verify_lp(self, key):
        """Verify the learning package was restored correctly."""
        lp = publishing_api.LearningPackage.objects.filter(key=key).first()
        assert lp is not None, "Learning package was not restored."
        assert lp.title == "Library test"
        assert lp.description == ""
        return lp

    def verify_containers(self, lp):
        """Verify the containers and their versions were restored correctly."""
        container_qs = publishing_api.get_containers(learning_package_id=lp.id)
        expected_container_keys = ["unit1-b7eafb", "subsection1-48afa3", "section1-8ca126"]

        for container in container_qs:
            assert container.key in expected_container_keys
            draft_version = publishing_api.get_draft_version(container.publishable_entity.id)
            published_version = publishing_api.get_published_version(container.publishable_entity.id)
            assert container.created_by is not None
            assert container.created_by.username == "lp_user"
            if container.key == "unit1-b7eafb":
                assert getattr(container, 'unit', None) is not None
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            elif container.key == "subsection1-48afa3":
                assert getattr(container, 'subsection', None) is not None
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            elif container.key == "section1-8ca126":
                assert getattr(container, 'section', None) is not None
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            else:
                assert False, f"Unexpected container key: {container.key}"

    def verify_components(self, lp):
        # pylint: disable=too-many-statements
        """Verify the components and their versions were restored correctly."""
        component_qs = components_api.get_components(lp.id)
        expected_component_keys = [
            "xblock.v1:drag-and-drop-v2:4d1b2fac-8b30-42fb-872d-6b10ab580b27",
            "xblock.v1:html:e32d5479-9492-41f6-9222-550a7346bc37",
            "xblock.v1:openassessment:1ee38208-a585-4455-a27e-4930aa541f53",
            "xblock.v1:problem:256739e8-c2df-4ced-bd10-8156f6cfa90b",
            "xblock.v1:survey:6681da3f-b056-4c6e-a8f9-040967907471",
            "xblock.v1:video:22601ebd-9da8-430b-9778-cfe059a98568",
            "xblock.v1:html:c22b9f97-f1e9-4e8f-87f0-d5a3c26083e2"
        ]
        for component in component_qs:
            assert component.key in expected_component_keys
            draft_version = publishing_api.get_draft_version(component.publishable_entity.id)
            published_version = publishing_api.get_published_version(component.publishable_entity.id)
            assert component.created_by is not None
            assert component.created_by.username == "lp_user"
            if component.key == "xblock.v1:drag-and-drop-v2:4d1b2fac-8b30-42fb-872d-6b10ab580b27":
                assert component.component_type.name == "drag-and-drop-v2"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
                # Get the content associated with this component
                contents = draft_version.componentversion.contents.all()
                content = contents.first() if contents.exists() else None
                assert content is not None
                assert "<drag-and-drop-v2" in content.text
                assert not content.has_file
                assert str(content.media_type) == "application/vnd.openedx.xblock.v1.drag-and-drop-v2+xml"
            elif component.key == "xblock.v1:html:e32d5479-9492-41f6-9222-550a7346bc37":
                assert component.component_type.name == "html"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 5
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is not None
                assert published_version.version_num == 4
                assert published_version.created_by is not None
                assert published_version.created_by.username == "lp_user"
            elif component.key == "xblock.v1:openassessment:1ee38208-a585-4455-a27e-4930aa541f53":
                assert component.component_type.name == "openassessment"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            elif component.key == "xblock.v1:problem:256739e8-c2df-4ced-bd10-8156f6cfa90b":
                assert component.component_type.name == "problem"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            elif component.key == "xblock.v1:survey:6681da3f-b056-4c6e-a8f9-040967907471":
                assert component.component_type.name == "survey"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 1
                assert published_version is None
            elif component.key == "xblock.v1:video:22601ebd-9da8-430b-9778-cfe059a98568":
                assert component.component_type.name == "video"
                assert component.component_type.namespace == "xblock.v1"
                assert draft_version is not None
                assert draft_version.version_num == 3
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is None
            elif component.key == "xblock.v1:html:c22b9f97-f1e9-4e8f-87f0-d5a3c26083e2":
                assert draft_version is not None
                assert draft_version.version_num == 2
                assert draft_version.created_by is not None
                assert draft_version.created_by.username == "lp_user"
                assert published_version is not None
                assert published_version.version_num == 2
                assert published_version.created_by is not None
                assert published_version.created_by.username == "lp_user"
            else:
                assert False, f"Unexpected component key: {component.key}"

    def verify_collections(self, lp):
        """Verify the collections were restored correctly."""
        collections = collections_api.get_collections(lp.id)
        assert collections.count() == 1
        collection = collections.first()
        assert collection.title == "Collection test1"
        assert collection.key == "collection-test"
        assert collection.description == ""
        assert collection.created_by is not None
        assert collection.created_by.username == "lp_user"

        expected_entity_keys = [
            "xblock.v1:html:e32d5479-9492-41f6-9222-550a7346bc37",
            "xblock.v1:problem:256739e8-c2df-4ced-bd10-8156f6cfa90b",
        ]
        entity_keys = [entity.key for entity in collection.entities.all()]
        assert set(entity_keys) == set(expected_entity_keys)


class RestoreLearningPackageTest(RestoreTestCase):
    """Tests for restoring learning packages without using the management command."""

    def test_successful_restore_with_no_command_line(self):
        """Test restoring a learning package without using the management command."""
        result = LearningPackageUnzipper(self.zip_file, key="lib-xx:WGU:LIB_C001").load()

        expected = {
            "status": "success",
            "log_file_error": None,
            "lp_restored_data": {
                "id": result["lp_restored_data"]["id"],  # Dynamic field
                "key": "lib-xx:WGU:LIB_C001",
                "archive_lp_key": "lib:WGU:LIB_C001",
                "archive_org_key": "WGU",
                "archive_slug": "LIB_C001",
                "title": "Library test",
                "num_containers": 3,
                "num_components": 7,
                "num_collections": 1,
                "num_sections": 1,
                "num_subsections": 1,
                "num_units": 1,
            },
            "backup_metadata": {
                "format_version": 1,
                "created_by": "lp_user",
                "created_at": datetime(2025, 10, 5, 18, 23, 45, 180535, tzinfo=timezone.utc),
                "origin_server": "cms.test",
            },
        }

        # Compare dicts except for dynamic fields
        assert result["status"] == expected["status"]
        assert result["log_file_error"] is None

        general_info = result["lp_restored_data"]
        expected_info = expected["lp_restored_data"]
        metadata_general_info = general_info.pop("backup_metadata", None)
        metadata_expected_info = expected_info.pop("backup_metadata", None)

        assert general_info == expected_info, f"General info does not match. Got {general_info}"
        assert metadata_general_info == metadata_expected_info, f"Meta info does not match. Got {metadata_general_info}"

        lp = publishing_api.LearningPackage.objects.filter(key="lib-xx:WGU:LIB_C001").first()
        assert lp is not None, "Learning package was not restored."

    def test_successful_restore_with_staged_key(self):
        """Test restoring a learning package with a staged key."""
        result = LearningPackageUnzipper(self.zip_file, user=self.user).load()

        assert result["status"] == "success"
        assert result["lp_restored_data"] is not None
        restored_key = result["lp_restored_data"]["key"]
        archive_key = result["lp_restored_data"]["archive_lp_key"]
        assert archive_key == "lib:WGU:LIB_C001"
        assert restored_key.startswith("lp-restore:lp_user:WGU:LIB_C001:")

        lp = publishing_api.LearningPackage.objects.filter(key=restored_key).first()
        assert lp is not None, "Learning package with staged key was not restored."

    def test_restore_with_missing_learning_package_file(self):
        """Test restoring a learning package with a missing learning_package.toml file."""
        zip_file = folder_to_inmemory_zip(os.path.join(os.path.dirname(__file__), "fixtures/missing_lp_file"))
        result = LearningPackageUnzipper(zip_file).load()

        assert result["status"] == "error"
        assert result["lp_restored_data"] is None
        assert result["log_file_error"] is not None
        log_content = result["log_file_error"].getvalue()
        assert "Missing learning package file." in log_content
        assert "Missing required learning_package.toml in archive." not in log_content

    def test_error_preliminary_check(self):
        """Test that preliminary check catches missing learning_package.toml."""
        zip_file = folder_to_inmemory_zip(os.path.join(os.path.dirname(__file__), "fixtures/missing_lp_file"))
        unzipper = LearningPackageUnzipper(zip_file)
        errors, _ = unzipper.check_mandatory_files()

        assert len(errors) == 1
        assert errors[0]["file"] == "package.toml"
        assert errors[0]["errors"] == "Missing learning package file."

    def test_error_learning_package_missing_key(self):
        """Test restoring a learning package with a learning_package.toml missing the 'key' field."""
        zip_file = folder_to_inmemory_zip(os.path.join(os.path.dirname(__file__), "fixtures/library_backup"))

        # Mock parse_learning_package_toml to return a dict without 'key'
        with patch(
            "openedx_content.applets.backup_restore.zipper.parse_learning_package_toml",
            return_value={
                "learning_package": {
                    "title": "Library test",
                    "description": "",
                    "created": "2025-09-03T17:50:59.536190Z",
                    "updated": "2025-09-03T17:50:59.536190Z",
                },
                "meta": {
                    "format_version": 1,
                    "created_by": "lp_user",
                    "created_at": "2025-09-03T17:50:59.536190Z",
                    "origin_server": "cms.test",
                },
            },
        ):
            result = LearningPackageUnzipper(zip_file).load()

        assert result["status"] == "error"
        assert result["lp_restored_data"] is None
        assert result["log_file_error"] is not None
        log_content = result["log_file_error"].getvalue()
        expected_error = "Errors encountered during restore:\npackage.toml learning package section: {'key':"
        assert expected_error in log_content

    def test_error_no_metadata_section(self):
        """Test restoring a learning package with a learning_package.toml missing the 'meta' section."""
        zip_file = folder_to_inmemory_zip(os.path.join(os.path.dirname(__file__), "fixtures/library_backup"))

        # Mock parse_learning_package_toml to return a dict without 'meta'
        with patch(
            "openedx_content.applets.backup_restore.zipper.parse_learning_package_toml",
            return_value={
                "learning_package": {
                    "title": "Library test",
                    "key": "lib:WGU:LIB_C001",
                    "description": "",
                    "created": "2025-09-03T17:50:59.536190Z",
                    "updated": "2025-09-03T17:50:59.536190Z",
                }
            },
        ):
            result = LearningPackageUnzipper(zip_file).load()

        assert result["status"] == "error"
        assert result["lp_restored_data"] is None
        assert result["log_file_error"] is not None
        log_content = result["log_file_error"].getvalue()
        expected_error = "Errors encountered during restore:\npackage.toml meta section: {'non_field_errors': [Er"
        assert expected_error in log_content

    def test_success_metadata_using_user_context(self):
        """Test that metadata is correctly extracted from learning_package.toml."""
        restore_result = LearningPackageUnzipper(self.zip_file, user=self.user).load()
        metadata = restore_result.get("backup_metadata", {})

        assert restore_result["status"] == "success"

        expected_metadata = {
            "format_version": 1,
            "created_by": "lp_user",
            "created_by_email": "lp_user@example.com",
            "created_at": datetime(2025, 10, 5, 18, 23, 45, 180535, tzinfo=timezone.utc),
            "original_server": "cms.test",
        }

        assert metadata == expected_metadata


class RestoreUtilitiesTest(TestCase):
    """Tests for utility functions used in the restore process."""

    def test_generate_staged_lp_key(self):
        """Test generating a staged learning package key."""

        user_mock = type("User", (), {"username": "dan"})
        lp_key = "lib:WGU:LIB_C001"
        staged_key = generate_staged_lp_key(lp_key, user_mock)

        assert staged_key.startswith("lp-restore:dan:WGU:LIB_C001:")
        parts = staged_key.split(":")
        assert len(parts) == 5
        timestamp_part = parts[-1]
        assert timestamp_part.isdigit()

    def test_error_generate_staged_lp_key_invalid_lp_key(self):
        """Test that generating a staged key with an invalid lp_key raises ValueError."""
        user_mock = type("User", (), {"username": "dan"})
        invalid_lp_key = "invalid-key-format"
        with self.assertRaises(ValueError) as context:
            generate_staged_lp_key(invalid_lp_key, user_mock)
        assert "Invalid learning package key" in str(context.exception)
