"""
Basic tests of the Collections API.
"""
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as UserType  # pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase
from freezegun import freeze_time

# Ensure our APIs and models are all exported to the package API.
from openedx_content import api
from openedx_content.models_api import (
    Collection,
    CollectionPublishableEntity,
    Component,
    ComponentType,
    LearningPackage,
    PublishableEntity,
    Unit,
)

User = get_user_model()


class CollectionTestCase(TestCase):
    """
    Base-class for setting up commonly used test data.
    """
    learning_package: LearningPackage
    learning_package_2: LearningPackage
    now: datetime

    @classmethod
    def setUpTestData(cls) -> None:
        cls.learning_package = api.create_learning_package(
            key="ComponentTestCase-test-key",
            title="Components Test Case Learning Package",
        )
        cls.learning_package_2 = api.create_learning_package(
            key="ComponentTestCase-test-key-2",
            title="Components Test Case another Learning Package",
        )
        cls.now = datetime(2024, 8, 5, tzinfo=timezone.utc)


class CollectionsTestCase(CollectionTestCase):
    """
    Base class with a bunch of collections pre-created.
    """
    collection1: Collection
    collection2: Collection
    collection3: Collection
    another_library_collection: Collection
    disabled_collection: Collection

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data (all our tests are read only).
        """
        super().setUpTestData()
        cls.collection1 = api.create_collection(
            cls.learning_package.id,
            key="COL1",
            created_by=None,
            title="Collection 1",
            description="Description of Collection 1",
        )
        cls.collection2 = api.create_collection(
            cls.learning_package.id,
            key="COL2",
            created_by=None,
            title="Collection 2",
            description="Description of Collection 2",
        )
        cls.collection3 = api.create_collection(
            cls.learning_package.id,
            key="COL3",
            created_by=None,
            title="Collection 3",
            description="Description of Collection 3",
        )
        cls.another_library_collection = api.create_collection(
            cls.learning_package_2.id,
            key="another_library",
            created_by=None,
            title="Collection 4",
            description="Description of Collection 4",
        )
        cls.disabled_collection = api.create_collection(
            cls.learning_package.id,
            key="disabled_collection",
            created_by=None,
            title="Disabled Collection",
            description="Description of Disabled Collection",
            enabled=False,
        )


class GetCollectionTestCase(CollectionsTestCase):
    """
    Test grabbing a queryset of Collections.
    """
    def test_get_collection(self):
        """
        Test getting a single collection.
        """
        collection = api.get_collection(self.learning_package.pk, 'COL1')
        assert collection == self.collection1

    def test_get_collection_not_found(self):
        """
        Test getting a collection that doesn't exist.
        """
        with self.assertRaises(ObjectDoesNotExist):
            api.get_collection(self.learning_package.pk, '12345')

    def test_get_collection_wrong_learning_package(self):
        """
        Test getting a collection that doesn't exist in the requested learning package.
        """
        with self.assertRaises(ObjectDoesNotExist):
            api.get_collection(self.learning_package.pk, self.another_library_collection.key)

    def test_get_collections(self):
        """
        Test getting all ENABLED collections for a learning package.
        """
        collections = api.get_collections(self.learning_package.id)
        assert list(collections) == [
            self.collection1,
            self.collection2,
            self.collection3,
        ]

    def test_get_invalid_collections(self):
        """
        Test getting collections for an invalid learning package should return an empty queryset.
        """
        collections = api.get_collections(12345)
        assert not list(collections)

    def test_get_all_collections(self):
        """
        Test getting all collections.
        """
        collections = api.get_collections(self.learning_package.id, enabled=None)
        self.assertQuerySetEqual(collections, [
            self.collection1,
            self.collection2,
            self.collection3,
            self.disabled_collection,
        ], ordered=True)

    def test_get_all_enabled_collections(self):
        """
        Test getting all ENABLED collections.
        """
        collections = api.get_collections(self.learning_package.id, enabled=True)
        self.assertQuerySetEqual(collections, [
            self.collection1,
            self.collection2,
            self.collection3,
        ], ordered=True)

    def test_get_all_disabled_collections(self):
        """
        Test getting all DISABLED collections.
        """
        collections = api.get_collections(self.learning_package.id, enabled=False)
        assert list(collections) == [self.disabled_collection]


class CollectionCreateTestCase(CollectionTestCase):
    """
    Test creating a collection.
    """

    def test_create_collection(self):
        """
        Test creating a collection.
        """
        user = User.objects.create(
            username="user",
            email="user@example.com",
        )
        created_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(created_time):
            collection = api.create_collection(
                self.learning_package.id,
                key='MYCOL',
                title="My Collection",
                created_by=user.id,
                description="This is my collection",
            )

        assert collection.title == "My Collection"
        assert collection.key == "MYCOL"
        assert collection.description == "This is my collection"
        assert collection.enabled
        assert collection.created == created_time
        assert collection.modified == created_time
        assert collection.created_by == user

    def test_create_collection_without_description(self):
        """
        Test creating a collection without a description.
        """
        collection = api.create_collection(
            self.learning_package.id,
            key='MYCOL',
            created_by=None,
            title="My Collection",
        )
        assert collection.title == "My Collection"
        assert collection.key == "MYCOL"
        assert collection.description == ""
        assert collection.enabled


class CollectionEntitiesTestCase(CollectionsTestCase):
    """
    Base class with collections that contain entities.
    """
    published_component: Component
    draft_component: Component
    draft_unit: Unit
    user: UserType
    html_type: ComponentType
    problem_type: ComponentType

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data
        """
        super().setUpTestData()

        cls.user = User.objects.create(
            username="user",
            email="user@example.com",
        )

        cls.html_type = api.get_or_create_component_type("xblock.v1", "html")
        cls.problem_type = api.get_or_create_component_type("xblock.v1", "problem")
        created_time = datetime(2025, 4, 1, tzinfo=timezone.utc)
        cls.draft_unit = api.create_unit(
            learning_package_id=cls.learning_package.id,
            key="unit-1",
            created=created_time,
            created_by=cls.user.id,
        )

        # Make and publish one Component
        cls.published_component, _ = api.create_component_and_version(
            cls.learning_package.id,
            cls.problem_type,
            local_key="my_published_example",
            title="My published problem",
            created=cls.now,
            created_by=cls.user.id,
        )
        api.publish_all_drafts(
            cls.learning_package.id,
            message="Publish from CollectionTestCase.setUpTestData",
            published_at=cls.now,
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

        # Add some shared components to the collections
        cls.collection1 = api.add_to_collection(
            cls.learning_package.id,
            key=cls.collection1.key,
            entities_qset=PublishableEntity.objects.filter(id__in=[
                cls.published_component.pk,
            ]),
            created_by=cls.user.id,
        )
        cls.collection2 = api.add_to_collection(
            cls.learning_package.id,
            key=cls.collection2.key,
            entities_qset=PublishableEntity.objects.filter(id__in=[
                cls.published_component.pk,
                cls.draft_component.pk,
                cls.draft_unit.pk,
            ]),
        )
        cls.disabled_collection = api.add_to_collection(
            cls.learning_package.id,
            key=cls.disabled_collection.key,
            entities_qset=PublishableEntity.objects.filter(id__in=[
                cls.published_component.pk,
            ]),
        )


class CollectionAddRemoveEntitiesTestCase(CollectionEntitiesTestCase):
    """
    Test collections that contain publishable entitites.
    """

    def test_create_collection_entities(self):
        """
        Ensure the collections were pre-populated with the expected publishable entities.
        """
        assert list(self.collection1.entities.all()) == [
            self.published_component.publishable_entity,
        ]
        assert list(self.collection2.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]
        assert not list(self.collection3.entities.all())
        assert not list(self.another_library_collection.entities.all())

    def test_add_to_collection(self):
        """
        Test adding entities to collections.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            self.collection1 = api.add_to_collection(
                self.learning_package.id,
                self.collection1.key,
                PublishableEntity.objects.filter(id__in=[
                    self.draft_component.pk,
                    self.draft_unit.pk,
                ]),
                created_by=self.user.id,
            )

        assert list(self.collection1.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]
        for collection_entity in CollectionPublishableEntity.objects.filter(collection=self.collection1):
            assert collection_entity.created_by == self.user
        assert self.collection1.modified == modified_time

    def test_add_to_collection_again(self):
        """
        Test that re-adding entities to a collection doesn't throw an error.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            self.collection2 = api.add_to_collection(
                self.learning_package.id,
                self.collection2.key,
                PublishableEntity.objects.filter(id__in=[
                    self.published_component.pk,
                ]),
            )

        assert list(self.collection2.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]
        assert self.collection2.modified == modified_time

    def test_add_to_collection_wrong_learning_package(self):
        """
        We cannot add entities to a collection from a different learning package.
        """
        with self.assertRaises(ValidationError):
            api.add_to_collection(
                self.learning_package_2.id,
                self.another_library_collection.key,
                PublishableEntity.objects.filter(id__in=[
                    self.published_component.pk,
                ]),
            )

        assert not list(self.another_library_collection.entities.all())

    def test_remove_from_collection(self):
        """
        Test removing entities from a collection.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            self.collection2 = api.remove_from_collection(
                self.learning_package.id,
                self.collection2.key,
                PublishableEntity.objects.filter(id__in=[
                    self.published_component.pk,
                    self.draft_unit.pk,
                ]),
            )

        assert list(self.collection2.entities.all()) == [
            self.draft_component.publishable_entity,
        ]
        assert self.collection2.modified == modified_time

    def test_get_entity_collections(self):
        """
        Tests fetching the enabled collections which contain a given entity.
        """
        collections = api.get_entity_collections(
            self.learning_package.id,
            self.published_component.publishable_entity.key,
        )
        assert list(collections) == [
            self.collection1,
            self.collection2,
        ]

    def test_get_collection_components(self):
        assert list(api.get_collection_components(
            self.learning_package.id,
            self.collection1.key,
        )) == [self.published_component]
        assert list(api.get_collection_components(
            self.learning_package.id,
            self.collection2.key,
        )) == [self.published_component, self.draft_component]
        assert not list(api.get_collection_components(
            self.learning_package.id,
            self.collection3.key,
        ))
        assert not list(api.get_collection_components(
            self.learning_package.id,
            self.another_library_collection.key,
        ))

    def test_get_collection_containers(self):
        assert not list(api.get_collection_containers(
            self.learning_package.id,
            self.collection1.key,
        ))
        assert list(api.get_collection_containers(
            self.learning_package.id,
            self.collection2.key,
        )) == [self.draft_unit.container]
        assert not list(api.get_collection_containers(
            self.learning_package.id,
            self.collection3.key,
        ))
        assert not list(api.get_collection_containers(
            self.learning_package.id,
            self.another_library_collection.key,
        ))


class UpdateCollectionTestCase(CollectionTestCase):
    """
    Test updating a collection.
    """
    collection: Collection

    @classmethod
    def setUpTestData(cls) -> None:
        """
        Initialize our content data
        """
        super().setUpTestData()
        cls.collection = api.create_collection(
            cls.learning_package.id,
            key="MYCOL",
            title="Collection",
            created_by=None,
            description="Description of Collection",
        )

    def test_update_collection(self):
        """
        Test updating a collection's title and description.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            collection = api.update_collection(
                self.learning_package.id,
                key=self.collection.key,
                title="New Title",
                description="",
            )

        assert collection.title == "New Title"
        assert collection.description == ""
        assert collection.modified == modified_time
        assert collection.created == self.collection.created  # unchanged

    def test_update_collection_partial(self):
        """
        Test updating a collection's title.
        """
        collection = api.update_collection(
            self.learning_package.id,
            key=self.collection.key,
            title="New Title",
        )

        assert collection.title == "New Title"
        assert collection.description == self.collection.description  # unchanged
        assert f"{collection}" == f"<Collection> (lp:{self.learning_package.id} {self.collection.key}:New Title)"

        collection = api.update_collection(
            self.learning_package.id,
            key=self.collection.key,
            description="New description",
        )

        assert collection.title == "New Title"  # unchanged
        assert collection.description == "New description"

    def test_update_collection_empty(self):
        """
        Test empty update.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            collection = api.update_collection(
                self.learning_package.id,
                key=self.collection.key,
            )

        assert collection.title == self.collection.title  # unchanged
        assert collection.description == self.collection.description  # unchanged
        assert collection.modified == self.collection.modified  # unchanged

    def test_update_collection_not_found(self):
        """
        Test updating a collection that doesn't exist.
        """
        with self.assertRaises(ObjectDoesNotExist):
            api.update_collection(
                self.learning_package.id,
                key="12345",
                title="New Title",
            )


class DeleteCollectionTestCase(CollectionEntitiesTestCase):
    """
    Tests soft-deleting, restoring, and deleting collections.
    """

    def test_soft_delete(self):
        """
        Collections are soft-deleted by default.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            collection = api.delete_collection(
                self.learning_package.id,
                key=self.collection2.key,
            )

        # Collection was disabled and still exists in the database
        assert not collection.enabled
        assert collection.modified == modified_time
        assert collection == api.get_collection(self.learning_package.id, collection.key)
        # ...and the collection's entities remain intact.
        assert list(collection.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]

    def test_delete(self):
        """
        Collections can be deleted completely.
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            collection = api.delete_collection(
                self.learning_package.id,
                key=self.collection2.key,
                hard_delete=True,
            )

        # Collection was returned unchanged, but it's been deleted
        assert collection.enabled
        assert not collection.id
        with self.assertRaises(ObjectDoesNotExist):
            api.get_collection(self.learning_package.id, collection.key)
        # ...and the entities have been removed from this collection
        assert list(api.get_entity_collections(
            self.learning_package.id,
            self.published_component.publishable_entity.key,
        )) == [self.collection1]
        assert not list(api.get_entity_collections(
            self.learning_package.id,
            self.draft_component.publishable_entity.key,
        ))

    def test_restore(self):
        """
        Soft-deleted collections can be restored.
        """
        collection = api.delete_collection(
            self.learning_package.id,
            key=self.collection2.key,
        )

        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            collection = api.restore_collection(
                self.learning_package.id,
                key=self.collection2.key,
            )

        # Collection was enabled and still exists in the database
        assert collection.enabled
        assert collection.modified == modified_time
        assert collection == api.get_collection(self.learning_package.id, collection.key)
        # ...and the collection's entities remain intact.
        assert list(collection.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]


class SetCollectionsTestCase(CollectionEntitiesTestCase):
    """
    Test setting multiple collections in a component.
    """
    def test_set_collections(self):
        """
        Test setting collections in a component
        """
        modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(modified_time):
            api.set_collections(
                self.draft_component.publishable_entity,
                collection_qset=Collection.objects.filter(id__in=[
                    self.collection1.pk,
                    self.collection2.pk,
                ]),
                created_by=self.user.id,
            )
        assert list(self.collection1.entities.all()) == [
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]
        assert list(self.collection2.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]

        for collection_entity in CollectionPublishableEntity.objects.filter(
            entity=self.draft_component.publishable_entity
        ):
            if collection_entity.collection == self.collection1:
                # The collection1 was newly associated, so it has a created_by
                assert collection_entity.created_by == self.user
            else:
                # The collection2 was already associated, with no created_by
                assert collection_entity.created_by is None

        # The collection1 was newly associated, so the modified time is set
        assert Collection.objects.get(id=self.collection1.pk).modified == modified_time
        # The collection2 was already associated, so the modified time is unchanged
        assert Collection.objects.get(id=self.collection2.pk).modified != modified_time

        # Set collections again, but this time remove collection1 and add collection3
        # Expected result: collection2 & collection3 associated to component and collection1 is excluded.
        new_modified_time = datetime(2024, 8, 8, tzinfo=timezone.utc)
        with freeze_time(new_modified_time):
            api.set_collections(
                self.draft_component.publishable_entity,
                collection_qset=Collection.objects.filter(id__in=[
                    self.collection3.pk,
                    self.collection2.pk,
                ]),
                created_by=self.user.id,
            )
        assert list(self.collection1.entities.all()) == [
            self.published_component.publishable_entity,
        ]
        assert list(self.collection2.entities.all()) == [
            self.draft_unit.publishable_entity,
            self.published_component.publishable_entity,
            self.draft_component.publishable_entity,
        ]
        assert list(self.collection3.entities.all()) == [
            self.draft_component.publishable_entity,
        ]
        # update modified time of all three collections as they were all updated
        assert Collection.objects.get(id=self.collection1.pk).modified == new_modified_time
        # collection2 was unchanged, so it should have the same modified time as before
        assert Collection.objects.get(id=self.collection2.pk).modified != new_modified_time
        assert Collection.objects.get(id=self.collection3.pk).modified == new_modified_time

    def test_set_collection_wrong_learning_package(self):
        """
        We cannot set collections with a different learning package than the component.
        """
        learning_package_3 = api.create_learning_package(
            key="ComponentTestCase-test-key-3",
            title="Components Test Case Learning Package-3",
        )
        collection = api.create_collection(
            learning_package_3.id,
            key="MYCOL",
            title="My Collection",
            created_by=None,
            description="Description of Collection",
        )

        with self.assertRaises(ValidationError):
            api.set_collections(
                self.draft_component.publishable_entity,
                collection_qset=Collection.objects.filter(id__in=[
                    collection.pk,
                ]),
                created_by=self.user.id,
            )

        assert list(self.collection1.entities.all()) == [
            self.published_component.publishable_entity,
        ]
