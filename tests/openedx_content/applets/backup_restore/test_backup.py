"""
Tests relating to dumping learning packages to disk
"""
import zipfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db.models import QuerySet
from django.test import TestCase

from openedx_content import api
from openedx_content.applets.backup_restore.zipper import LearningPackageZipper
from openedx_content.models_api import Collection, Component, Content, LearningPackage, PublishableEntity

User = get_user_model()


class LpDumpCommandTestCase(TestCase):
    """
    Test the lp_dump management command.
    """

    learning_package: LearningPackage
    all_components: QuerySet[PublishableEntity]
    now: datetime
    xblock_v1_namespace: str
    html_type: str
    problem_type: str
    published_component: Component
    published_component2: Component
    draft_component: Component
    html_asset_content: Content
    collection: Collection

    @classmethod
    def setUpTestData(cls):
        """
        Initialize data for the whole TestCase
        """

        # Create a user for the test
        cls.user = User.objects.create(
            username="user",
            first_name="Learning",
            last_name="Package User",
            email="user@example.com",
        )

        # Create a Learning Package for the test
        cls.learning_package = api.create_learning_package(
            key="ComponentTestCase-test-key",
            title="Components Test Case Learning Package",
            description="This is a test learning package for components.",
        )
        cls.now = datetime(2024, 8, 5, tzinfo=timezone.utc)

        cls.xblock_v1_namespace = "xblock.v1"
        html_media_type = api.get_or_create_media_type("text/html")
        text_media_type = api.get_or_create_media_type("text/plain")

        # Create component types
        cls.html_type = api.get_or_create_component_type(cls.xblock_v1_namespace, "html")
        cls.problem_type = api.get_or_create_component_type(cls.xblock_v1_namespace, "problem")

        # Make and publish one Component
        cls.published_component, _ = api.create_component_and_version(
            cls.learning_package.id,
            cls.problem_type,
            local_key="my_published_example",
            title="My published problem",
            created=cls.now,
            created_by=cls.user.id,
        )

        # Make and publish one Component
        # Same local key as above to test uniqueness of slugified hash
        cls.published_component2, _ = api.create_component_and_version(
            cls.learning_package.id,
            cls.problem_type,
            local_key="My_published_example",
            title="My published problem 2",
            created=cls.now,
            created_by=cls.user.id,
        )

        # Create a Content entry for the published Component
        api.publish_all_drafts(
            cls.learning_package.id,
            message="Publish from CollectionTestCase.setUpTestData",
            published_at=cls.now,
        )

        new_problem_version = api.create_next_component_version(
            cls.published_component.pk,
            title="My published problem draft v2",
            content_to_replace={},
            created=cls.now,
        )

        new_txt_content = api.get_or_create_text_content(
            cls.learning_package.pk,
            text_media_type.id,
            text="This is some data",
            created=cls.now,
        )
        api.create_component_version_content(
            new_problem_version.pk,
            new_txt_content.pk,
            key="hello.txt",
        )

        # Create a Draft component, one in each learning package
        cls.draft_component, _ = api.create_component_and_version(
            cls.learning_package.id,
            cls.html_type,
            local_key="my_draft_example",
            title="My draft html",
            created=cls.now,
            created_by=cls.user.id,
        )

        new_html_version = api.create_next_component_version(
            cls.draft_component.pk,
            title="My draft html v2",
            content_to_replace={},
            created=cls.now,
        )

        cls.html_asset_content = api.get_or_create_file_content(
            cls.learning_package.id,
            html_media_type.id,
            data=b"<html>hello world!</html>",
            created=cls.now,
        )
        api.create_component_version_content(
            new_html_version.pk,
            cls.html_asset_content.id,
            key="static/other/subdirectory/hello.html",
        )

        components = api.get_publishable_entities(cls.learning_package)
        cls.all_components = components

        cls.collection = api.create_collection(
            cls.learning_package.id,
            key="COL1",
            created_by=cls.user.id,
            title="Collection 1",
            description="Description of Collection 1",
        )

        api.add_to_collection(
            cls.learning_package.id,
            cls.collection.key,
            components
        )

        api.create_unit(
            learning_package_id=cls.learning_package.id,
            key="unit-1",
            created=cls.now,
            created_by=cls.user.id,
        )

    def check_toml_file(self, zip_path: Path, zip_member_name: Path, content_to_check: list):
        """
        Check that a specific entity TOML file in the zip matches the expected content.
        """
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            with zip_file.open(str(zip_member_name)) as toml_file:
                toml_content = toml_file.read().decode("utf-8")
                for value in content_to_check:
                    self.assertIn(value, toml_content)

    def check_zip_file_structure(self, zip_path: Path):
        """
        Check that the zip file has the expected structure.
        """
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            # Check that the zip file contains the expected files

            expected_directories = [
                "collections/",
                "entities/xblock.v1/html/my_draft_example/component_versions/v2/static/",
                "entities/xblock.v1/problem/my_published_example/component_versions/v1/static/",
                "entities/xblock.v1/problem/my_published_example_386dce/component_versions/v1/static/",
                "entities/xblock.v1/problem/my_published_example_386dce/component_versions/v2/static/",
            ]

            expected_files = [
                # Learning package files
                "package.toml",

                # Entity TOML files
                "entities/xblock.v1/html/my_draft_example.toml",
                "entities/xblock.v1/problem/my_published_example.toml",
                "entities/xblock.v1/problem/my_published_example_386dce.toml",

                # Entity static content files
                "entities/xblock.v1/html/my_draft_example/component_versions/v2/static/other/subdirectory/hello.html",
                "entities/xblock.v1/problem/my_published_example_386dce/component_versions/v2/hello.txt",

                # Collections
                "collections/col1.toml",
            ]

            expected_paths = expected_directories + expected_files

            # Check that all expected paths are present
            zip_name_list = zip_file.namelist()
            for expected_path in expected_paths:
                self.assertIn(expected_path, zip_name_list)

    def test_lp_dump_command(self):
        lp_key = self.learning_package.key
        file_name = f"{lp_key}.zip"
        try:
            out = StringIO()

            origin_server = "http://origin.server"

            # Call the management command to dump the learning package
            call_command(
                "lp_dump", lp_key, file_name, username=self.user.username, origin_server=origin_server, stdout=out
            )

            # Check that the zip file was created
            self.assertTrue(Path(file_name).exists())

            # Check the structure of the zip file
            self.check_zip_file_structure(Path(file_name))

            zip_path = Path(file_name)

            # Check the content of the package.toml file
            self.check_toml_file(
                zip_path,
                Path("package.toml"),
                [
                    '[learning_package]',
                    f'key = "{self.learning_package.key}"',
                    f'title = "{self.learning_package.title}"',
                    f'description = "{self.learning_package.description}"',
                    '[meta]',
                    'format_version = 1',
                    'origin_server = "http://origin.server"',
                    'created_at =',
                    'created_by = "user"',
                    'created_by_email = "user@example.com"',
                ]
            )

            # Check the content of the entity TOML files
            expected_files = {
                "entities/xblock.v1/problem/my_published_example_386dce.toml": [
                    '[entity]',
                    'can_stand_alone = true',
                    '[entity.draft]',
                    'version_num = 2',
                    '[entity.published]',
                    'version_num = 1',
                ],
                "entities/xblock.v1/html/my_draft_example.toml": [
                    '[entity]',
                    'can_stand_alone = true',
                    '[entity.draft]',
                    'version_num = 2',
                    '[entity.published]',
                    '# unpublished: no published_version_num',
                ],
            }

            for file_path, expected_content in expected_files.items():
                self.check_toml_file(zip_path, Path(file_path), expected_content)

            # Check the output message
            message = f'{lp_key} written to {file_name}'
            self.assertIn(message, out.getvalue())
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.fail(f"lp_dump command failed with error: {e}")
        finally:
            # Clean up the created zip file
            if Path(file_name).exists():
                Path(file_name).unlink(missing_ok=True)

    def test_dump_nonexistent_learning_package(self):
        out = StringIO()
        lp_key = "nonexistent_lp"
        file_name = f"{lp_key}.zip"
        with self.assertRaises(CommandError):
            # Attempt to dump a learning package that does not exist
            call_command("lp_dump", lp_key, file_name, stdout=out)
            self.assertIn("Learning package 'nonexistent_lp' does not exist", out.getvalue())

    def test_queries_n_plus_problem(self):
        """
        Test n plus problem over LearningPackageZipper for performance.
        Regardless of the number of entities, the query count should remain on 3
        Why?
            1 query for PublishableEntity + select_related joins
            1 query for all draft contents
            1 query for all published contents
        """
        dummy_path = "dummy/path"
        zipper = LearningPackageZipper(self.learning_package, dummy_path)
        entities = zipper.get_publishable_entities()
        with self.assertNumQueries(3):
            list(entities)  # force evaluation
            self.assertEqual(len(entities), 4)
        # Add another component
        api.create_component_and_version(
            self.learning_package.id,
            self.problem_type,
            local_key="my_published_example2",
            title="My published problem 2",
            created=self.now,
            created_by=self.user.id,
        )
        entities = zipper.get_publishable_entities()
        with self.assertNumQueries(3):
            list(entities)  # force evaluation
            self.assertEqual(len(entities), 5)
