"""
Tests related to the Component models
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, assert_type

from django.test import TestCase
from freezegun import freeze_time

from openedx_content.applets.components.api import (
    create_component_and_version,
    get_component,
    get_or_create_component_type,
)
from openedx_content.applets.components.models import Component, ComponentType, ComponentVersion
from openedx_content.applets.publishing.api import (
    LearningPackage,
    create_learning_package,
    create_publishable_entity_version,
    publish_all_drafts,
)

if TYPE_CHECKING:
    # Test that our mixins on Component.objects and PublishableEntityVersionMixin etc. haven't broken manager typing
    assert_type(Component.objects.create(), Component)
    assert_type(Component.objects.get(), Component)
    assert_type(Component.with_publishing_relations.create(), Component)
    assert_type(Component.with_publishing_relations.get(), Component)
    assert_type(ComponentVersion.objects.create(), ComponentVersion)
    assert_type(ComponentVersion.objects.get(), ComponentVersion)


class TestModelVersioningQueries(TestCase):
    """
    Test that Component/ComponentVersion are registered with the publishing app.
    """
    learning_package: LearningPackage
    now: datetime
    problem_type: ComponentType

    @classmethod
    def setUpTestData(cls) -> None:  # Note: we must specify '-> None' to opt in to type checking
        cls.learning_package = create_learning_package(
            "components.TestVersioning",
            "Learning Package for Testing Component Versioning",
        )
        cls.now = datetime(2023, 5, 8, tzinfo=timezone.utc)
        cls.problem_type = get_or_create_component_type("xblock.v1", "problem")

    def test_latest_version(self) -> None:
        component, component_version = create_component_and_version(
            self.learning_package.id,
            component_type=self.problem_type,
            local_key="monty_hall",
            title="Monty Hall Problem",
            created=self.now,
            created_by=None,
        )
        assert component.versioning.draft == component_version
        assert component.versioning.published is None
        publish_all_drafts(self.learning_package.pk, published_at=self.now)

        # Publishing isn't immediately reflected in the component obj (it's
        # using a cached version).
        assert component.versioning.published is None

        # Re-fetching the component and the published version should be updated.
        component = get_component(component.pk)
        assert component.versioning.published == component_version

        # Grabbing the list of versions for this component
        assert list(component.versioning.versions) == [component_version]

        # Grab a specific version by number
        assert component.versioning.version_num(1) == component_version

    def test_last_publish_log(self):
        """
        Test last_publish_log versioning property for published Components.
        """
        # This Component will get a couple of Published versions
        component_with_changes, _ = create_component_and_version(
            self.learning_package.id,
            component_type=self.problem_type,
            local_key="with_changes",
            title="Component with changes v1",
            created=self.now,
            created_by=None,
        )

        # This Component will only be Published once.
        component_with_no_changes, _ = create_component_and_version(
            self.learning_package.id,
            component_type=self.problem_type,
            local_key="with_no_changes",
            title="Component with no changes v1",
            created=self.now,
            created_by=None,
        )

        # Publish first time.
        published_first_time = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        with freeze_time(published_first_time):
            publish_all_drafts(self.learning_package.id)

        # Refetch the entities to get latest versions
        component_with_changes = get_component(component_with_changes.pk)
        component_with_no_changes = get_component(component_with_no_changes.pk)

        # Fetch the most recent PublishLog for these components
        first_publish_log_for_component_with_changes = component_with_changes.versioning.last_publish_log
        first_publish_log_for_component_with_no_changes = component_with_no_changes.versioning.last_publish_log

        # PublishLog for library + both entities should match each other
        assert (
            published_first_time ==
            first_publish_log_for_component_with_changes.published_at ==
            first_publish_log_for_component_with_no_changes.published_at
        )

        # Modify component_with_changes
        create_publishable_entity_version(
            component_with_changes.publishable_entity.id,
            version_num=2,
            title="Component with changes v2",
            created=self.now,
            created_by=None,
        )

        # Publish second time
        published_second_time = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        with freeze_time(published_second_time):
            publish_all_drafts(self.learning_package.id)

        # Refetch the entities to get latest versions
        component_with_changes = get_component(component_with_changes.pk)
        component_with_no_changes = get_component(component_with_no_changes.pk)

        # Re-fetch the most recent PublishLog for these components
        next_publish_log_for_component_with_changes = component_with_changes.versioning.last_publish_log
        next_publish_log_for_component_with_no_changes = component_with_no_changes.versioning.last_publish_log

        # PublishLog for component_with_changes should have been updated
        assert (
            published_second_time ==
            next_publish_log_for_component_with_changes.published_at
        )
        # But the component_with_no_changes should still be on the original publish log
        assert (
            first_publish_log_for_component_with_no_changes ==
            next_publish_log_for_component_with_no_changes
        )
