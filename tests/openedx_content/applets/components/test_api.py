"""
Basic tests of the Components API.
"""
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

from openedx_content.applets.collections import api as collection_api
from openedx_content.applets.collections.models import Collection
from openedx_content.applets.components import api as components_api
from openedx_content.applets.components.models import Component, ComponentType
from openedx_content.applets.contents import api as contents_api
from openedx_content.applets.contents.models import MediaType
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import LearningPackage

User = get_user_model()


class ComponentTestCase(TestCase):
    """
    Base-class for setting up commonly used test data.
    """
    learning_package: LearningPackage
    now: datetime

    # XBlock Component Types
    html_type: ComponentType
    problem_type: ComponentType
    video_type: ComponentType

    @classmethod
    def setUpTestData(cls) -> None:
        cls.learning_package = publishing_api.create_learning_package(
            key="ComponentTestCase-test-key",
            title="Components Test Case Learning Package",
        )
        cls.now = datetime(2023, 5, 8, tzinfo=timezone.utc)
        cls.html_type = components_api.get_or_create_component_type("xblock.v1", "html")
        cls.problem_type = components_api.get_or_create_component_type("xblock.v1", "problem")
        cls.video_type = components_api.get_or_create_component_type("xblock.v1", "video")

    def publish_component(self, component: Component):
        """
        Helper method to publish a single component.
        """
        publishing_api.publish_from_drafts(
            self.learning_package.pk,
            draft_qset=publishing_api.get_all_drafts(self.learning_package.pk).filter(
                entity=component.publishable_entity,
            ),
        )


class PerformanceTestCase(ComponentTestCase):
    """
    Performance related tests for Components.

    These are mostly to ensure that when Components are fetched, they're fetched
    with a select_related on the most commonly queried things; draft and
    published version metadata.
    """
    learning_package: LearningPackage
    now: datetime

    def test_component_num_queries(self) -> None:
        """
        Create a basic component and test that we fetch it back in 1 query.
        """
        component, _version = components_api.create_component_and_version(
            self.learning_package.id,
            component_type=self.problem_type,
            local_key="Query Counting",
            title="Querying Counting Problem",
            created=self.now,
            created_by=None,
        )
        publishing_api.publish_all_drafts(
            self.learning_package.pk,
            published_at=self.now
        )

        # We should be fetching all of this with a select-related, so only one
        # database query should happen here.
        with self.assertNumQueries(1):
            component = components_api.get_component(component.pk)
            draft = component.versioning.draft
            published = component.versioning.published
            assert draft.title == published.title
            assert component.versioning.last_publish_log.published_at == self.now


class GetComponentsTestCase(ComponentTestCase):
    """
    Test grabbing a queryset of Components.
    """
    published_problem: Component
    published_html: Component
    unpublished_problem: Component
    unpublished_html: Component
    deleted_video: Component

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data (all our tests are read only).

        We don't actually need to add content to the ComponentVersions, since
        for this we only care about the metadata on Components, their versions,
        and the associated draft/publish status.
        """
        super().setUpTestData()
        v2_problem_type = components_api.get_or_create_component_type("xblock.v2", "problem")

        cls.published_problem, _version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=v2_problem_type,
            local_key="pp_lk",
            title="Published Problem",
            created=cls.now,
            created_by=None,
        )
        cls.published_html, _version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=cls.html_type,
            local_key="ph_lk",
            title="Published HTML",
            created=cls.now,
            created_by=None,
        )
        publishing_api.publish_all_drafts(
            cls.learning_package.pk,
            published_at=cls.now
        )

        # Components that exist only as Drafts
        cls.unpublished_problem, _version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=v2_problem_type,
            local_key="upp_lk",
            title="Unpublished Problem",
            created=cls.now,
            created_by=None,
        )
        cls.unpublished_html, _version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=cls.html_type,
            local_key="uph_lk",
            title="Unpublished HTML",
            created=cls.now,
            created_by=None,
        )

        # Component we're putting here to soft delete (this will remove the
        # Draft entry)
        cls.deleted_video, _version = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=cls.video_type,
            local_key="dv_lk",
            title="Deleted Video",
            created=cls.now,
            created_by=None,
        )
        publishing_api.soft_delete_draft(cls.deleted_video.pk)

    def test_no_filters(self):
        """
        Test that we pull back everything, even unpublished or "deleted" items.
        """
        all_components = components_api.get_components(self.learning_package.id)
        assert list(all_components) == [
            self.published_problem,
            self.published_html,
            self.unpublished_problem,
            self.unpublished_html,
            self.deleted_video,
        ]

    def test_draft_filter(self):
        """
        Test the draft flag.
        """
        components_with_draft_version = components_api.get_components(
            self.learning_package.id,
            draft=True,
        )
        assert list(components_with_draft_version) == [
            self.published_problem,
            self.published_html,
            self.unpublished_problem,
            self.unpublished_html
        ]

        components_without_draft_version = components_api.get_components(
            self.learning_package.id,
            draft=False,
        )
        assert list(components_without_draft_version) == [
            self.deleted_video
        ]

    def test_published_filter(self):
        """
        Test the published filter.
        """
        components_with_published_version = components_api.get_components(
            self.learning_package.id,
            published=True,
        )
        assert list(components_with_published_version) == [
            self.published_problem,
            self.published_html,
        ]
        components_without_published_version = components_api.get_components(
            self.learning_package.id,
            published=False,
        )
        assert list(components_without_published_version) == [
            self.unpublished_problem,
            self.unpublished_html,
            self.deleted_video,
        ]

    def test_namespace_filter(self):
        """
        Test the namespace filter.

        Note that xblock.v2 is being used to test filtering, but there's nothing
        that's actually in the system for xblock.v2 at the moment.
        """
        components_with_xblock_v2 = components_api.get_components(
            self.learning_package.id,
            namespace='xblock.v2',
        )
        assert list(components_with_xblock_v2) == [
            self.published_problem,
            self.unpublished_problem,
        ]

    def test_types_filter(self):
        """
        Test the types filter.
        """
        html_and_video_components = components_api.get_components(
            self.learning_package.id,
            type_names=['html', 'video']
        )
        assert list(html_and_video_components) == [
            self.published_html,
            self.unpublished_html,
            self.deleted_video,
        ]

    def test_draft_title_filter(self):
        """
        Test the title filter.

        Note that this should be doing a case-insensitive match.
        """
        components = components_api.get_components(
            self.learning_package.id,
            draft_title="PUBLISHED"
        )
        # These all have a draft title with "published" in it somewhere.
        assert list(components) == [
            self.published_problem,
            self.published_html,
            self.unpublished_problem,
            self.unpublished_html,
        ]

    def test_published_title_filter(self):
        """
        Test the title filter.

        Note that this should be doing a case-insensitive match.
        """
        components = components_api.get_components(
            self.learning_package.id,
            published_title="problem"
        )
        # These all have a published title with "problem" in it somewhere,
        # meaning that it won't pick up the components that only exist as
        # drafts.
        assert list(components) == [
            self.published_problem,
        ]


class ComponentGetAndExistsTestCase(ComponentTestCase):
    """
    Test getting a Component by primary key or key string.
    """
    problem: Component
    html: Component

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data (all our tests are read only).

        We don't actually need to add content to the ComponentVersions, since
        for this we only care about the metadata on Compnents, their versions,
        and the associated draft/publish status.
        """
        super().setUpTestData()

        cls.problem = components_api.create_component(
            cls.learning_package.id,
            component_type=cls.problem_type,
            local_key='my_component',
            created=cls.now,
            created_by=None,
        )
        cls.html = components_api.create_component(
            cls.learning_package.id,
            component_type=cls.html_type,
            local_key='my_component',
            created=cls.now,
            created_by=None,
            can_stand_alone=False,
        )

    def test_simple_get(self):
        assert components_api.get_component(self.problem.pk) == self.problem
        with self.assertRaises(ObjectDoesNotExist):
            components_api.get_component(-1)

    def test_publishing_entity_key_convention(self):
        """Our mapping convention is {namespace}:{component_type}:{local_key}"""
        assert self.problem.key == "xblock.v1:problem:my_component"

    def test_stand_alone_flag(self):
        """Check if can_stand_alone flag is set"""
        component = components_api.get_component_by_key(
            self.learning_package.id,
            namespace='xblock.v1',
            type_name='html',
            local_key='my_component',
        )
        assert not component.publishable_entity.can_stand_alone

    def test_get_by_key(self):
        assert self.html == components_api.get_component_by_key(
            self.learning_package.id,
            namespace='xblock.v1',
            type_name='html',
            local_key='my_component',
        )
        with self.assertRaises(ObjectDoesNotExist):
            components_api.get_component_by_key(
                self.learning_package.id,
                namespace='xblock.v1',
                type_name='video',  # 'video' doesn't match anything we have
                local_key='my_component',
            )

    def test_exists_by_key(self):
        assert components_api.component_exists_by_key(
            self.learning_package.id,
            namespace='xblock.v1',
            type_name='problem',
            local_key='my_component',
        )
        assert not components_api.component_exists_by_key(
            self.learning_package.id,
            namespace='xblock.v1',
            type_name='problem',
            local_key='not_my_component',
        )


class CreateNewVersionsTestCase(ComponentTestCase):
    """
    Create new ComponentVersions in various ways.
    """
    problem: Component
    text_media_type: MediaType

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.problem = components_api.create_component(
            cls.learning_package.id,
            component_type=cls.problem_type,
            local_key='my_component',
            created=cls.now,
            created_by=None,
        )
        cls.text_media_type = contents_api.get_or_create_media_type("text/plain")

    def test_add(self):
        new_version = components_api.create_component_version(
            self.problem.pk,
            version_num=1,
            title="My Title",
            created=self.now,
            created_by=None,
        )
        new_content = contents_api.get_or_create_text_content(
            self.learning_package.pk,
            self.text_media_type.id,
            text="This is some data",
            created=self.now,
        )
        components_api.create_component_version_content(
            new_version.pk,
            new_content.pk,
            key="my/path/to/hello.txt",
        )
        # re-fetch from the database to check to see if we wrote it correctly
        new_version = components_api.get_component(self.problem.pk) \
                                    .versions \
                                    .get(publishable_entity_version__version_num=1)
        assert (
            new_content ==
            new_version.contents.get(componentversioncontent__key="my/path/to/hello.txt")
        )

        # Write the same content again, but to an absolute path (should auto-
        # strip) the leading '/'s.
        components_api.create_component_version_content(
            new_version.pk,
            new_content.pk,
            key="//nested/path/hello.txt",
        )
        new_version = components_api.get_component(self.problem.pk) \
                                    .versions \
                                    .get(publishable_entity_version__version_num=1)
        assert (
            new_content ==
            new_version.contents.get(componentversioncontent__key="nested/path/hello.txt")
        )

    def test_bytes_content(self):
        bytes_content = b'raw content'

        version_1 = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 1",
            content_to_replace={
                "raw.txt": bytes_content,
                "no_ext": bytes_content,
            },
            created=self.now,
        )

        content_txt = version_1.contents.get(componentversioncontent__key="raw.txt")
        content_raw_txt = version_1.contents.get(componentversioncontent__key="no_ext")

        assert content_txt.size == len(bytes_content)
        assert str(content_txt.media_type) == 'text/plain'
        assert content_txt.read_file().read() == bytes_content

        assert content_raw_txt.size == len(bytes_content)
        assert str(content_raw_txt.media_type) == 'application/octet-stream'
        assert content_raw_txt.read_file().read() == bytes_content

    def test_multiple_versions(self):
        hello_content = contents_api.get_or_create_text_content(
            self.learning_package.id,
            self.text_media_type.id,
            text="Hello World!",
            created=self.now,
        )
        goodbye_content = contents_api.get_or_create_text_content(
            self.learning_package.id,
            self.text_media_type.id,
            text="Goodbye World!",
            created=self.now,
        )
        blank_content = contents_api.get_or_create_text_content(
            self.learning_package.id,
            self.text_media_type.id,
            text="",
            created=self.now,
        )

        # Two text files, hello.txt and goodbye.txt
        version_1 = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 1",
            content_to_replace={
                "hello.txt": hello_content.pk,
                "goodbye.txt": goodbye_content.pk,
            },
            created=self.now,
        )
        assert version_1.version_num == 1
        assert version_1.title == "Problem Version 1"
        version_1_contents = list(version_1.contents.all())
        assert len(version_1_contents) == 2
        assert (
            hello_content ==
            version_1.contents
                     .get(componentversioncontent__key="hello.txt")
        )
        assert (
            goodbye_content ==
            version_1.contents
                     .get(componentversioncontent__key="goodbye.txt")
        )

        # This should keep the old value for goodbye.txt, add blank.txt, and set
        # hello.txt to be a new value (blank).
        version_2 = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 2",
            content_to_replace={
                "hello.txt": blank_content.pk,
                "blank.txt": blank_content.pk,
            },
            created=self.now,
        )
        assert version_2.version_num == 2
        assert version_2.contents.count() == 3
        assert (
            blank_content ==
            version_2.contents
                     .get(componentversioncontent__key="hello.txt")
        )
        assert (
            goodbye_content ==
            version_2.contents
                     .get(componentversioncontent__key="goodbye.txt")
        )
        assert (
            blank_content ==
            version_2.contents
                     .get(componentversioncontent__key="blank.txt")
        )

        # Now we're going to set "hello.txt" back to hello_content, but remove
        # blank.txt, goodbye.txt, and an unknown "nothere.txt".
        version_3 = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 3",
            content_to_replace={
                "hello.txt": hello_content.pk,
                "blank.txt": None,
                "goodbye.txt": None,
                "nothere.txt": None,  # should not error
            },
            created=self.now,
        )
        assert version_3.version_num == 3
        assert version_3.contents.count() == 1
        assert (
            hello_content ==
            version_3.contents
                     .get(componentversioncontent__key="hello.txt")
        )

    def test_create_next_version_forcing_num_version(self):
        """Test creating a next version with a forced version number."""
        version_1 = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 1",
            content_to_replace={},
            created=self.now,
            force_version_num=5,
        )
        assert version_1.version_num == 5

    def test_create_multiple_next_versions_and_diff_content(self):
        """
        Test creating multiple next versions with different content.
        This includes a case where we want to ignore previous content.
        """
        python_source_media_type = contents_api.get_or_create_media_type(
            "text/x-python",
        )
        python_source_asset = contents_api.get_or_create_file_content(
            self.learning_package.id,
            python_source_media_type.id,
            data=b"print('hello world!')",
            created=self.now,
        )
        content_to_replace_for_published = {
            'static/profile.webp': python_source_asset.pk,
            'static/background.webp': python_source_asset.pk,
        }

        content_to_replace_for_draft = {
            'static/profile.webp': python_source_asset.pk,
            'static/new_file.webp': python_source_asset.pk,
        }
        version_1_published = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 1",
            content_to_replace=content_to_replace_for_published,
            created=self.now,
        )
        assert version_1_published.version_num == 1

        publishing_api.publish_all_drafts(
            self.learning_package.pk,
            published_at=self.now
        )

        version_2_draft = components_api.create_next_component_version(
            self.problem.pk,
            title="Problem Version 2",
            content_to_replace=content_to_replace_for_draft,
            created=self.now,
            ignore_previous_content=True,
        )
        assert version_2_draft.version_num == 2
        assert version_2_draft.contents.count() == 2
        assert (
            python_source_asset ==
            version_2_draft.contents.get(
                componentversioncontent__key="static/profile.webp")
        )
        assert (
            python_source_asset ==
            version_2_draft.contents.get(
                componentversioncontent__key="static/new_file.webp")
        )
        with self.assertRaises(ObjectDoesNotExist):
            # This file was in the published version, but not in the draft version
            # since we ignored previous content.
            version_2_draft.contents.get(componentversioncontent__key="static/background.webp")


class SetCollectionsTestCase(ComponentTestCase):
    """
    Test setting collections for a component.
    """
    collection1: Collection
    collection2: Collection
    collection3: Collection
    published_problem: Component
    user: UserType

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize some collections
        """
        super().setUpTestData()
        v2_problem_type = components_api.get_or_create_component_type("xblock.v2", "problem")
        cls.published_problem, _ = components_api.create_component_and_version(
            cls.learning_package.id,
            component_type=v2_problem_type,
            local_key="pp_lk",
            title="Published Problem",
            created=cls.now,
            created_by=None,
        )
        cls.collection1 = collection_api.create_collection(
            cls.learning_package.id,
            key="MYCOL1",
            title="Collection1",
            created_by=None,
            description="Description of Collection 1",
        )
        cls.collection2 = collection_api.create_collection(
            cls.learning_package.id,
            key="MYCOL2",
            title="Collection2",
            created_by=None,
            description="Description of Collection 2",
        )
        cls.collection3 = collection_api.create_collection(
            cls.learning_package.id,
            key="MYCOL3",
            title="Collection3",
            created_by=None,
            description="Description of Collection 3",
        )
        cls.user = User.objects.create(
            username="user",
            email="user@example.com",
        )


class TestComponentTypeUtils(TestCase):
    """
    Test the component type utility functions.
    """

    def test_get_or_create_component_type_by_entity_key_creates_new(self):
        comp_type, local_key = components_api.get_or_create_component_type_by_entity_key(
            "video:youtube:abcd1234"
        )

        assert isinstance(comp_type, ComponentType)
        assert comp_type.namespace == "video"
        assert comp_type.name == "youtube"
        assert local_key == "abcd1234"
        assert ComponentType.objects.count() == 1

    def test_get_or_create_component_type_by_entity_key_existing(self):
        ComponentType.objects.create(namespace="video", name="youtube")

        comp_type, local_key = components_api.get_or_create_component_type_by_entity_key(
            "video:youtube:efgh5678"
        )

        assert comp_type.namespace == "video"
        assert comp_type.name == "youtube"
        assert local_key == "efgh5678"
        assert ComponentType.objects.count() == 1

    def test_get_or_create_component_type_by_entity_key_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            components_api.get_or_create_component_type_by_entity_key("not-enough-parts")

        self.assertIn("Invalid entity_key format", str(ctx.exception))
