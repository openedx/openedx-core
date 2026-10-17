"""
Basic tests for the publishing containers API.
"""
# pylint: disable=too-many-positional-arguments, unused-argument

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, assert_type, cast

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from openedx_content.applets.containers import api as containers_api
from openedx_content.applets.containers.models import Container, ContainerType, ContainerVersion
from openedx_content.applets.publishing import api as publishing_api
from openedx_content.applets.publishing.models import (
    LearningPackage,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersionMixin,
    PublishLog,
)
from tests.test_django_app.models import (
    ContainerContainer,
    TestContainer,
    TestContainerVersion,
    TestEntity,
    TestEntityVersion,
)

# Note: to test the Publishing applet in isolation, this test suite does not import "Component", "Unit", or other models
# from applets that build on this one. Since Containers require specific concrete container types, we use
# "TestContainer" and "ContainerContainer" from test_django_app, which are specifically for testing the publishing
# API.


pytestmark = pytest.mark.django_db
now = datetime(2026, 5, 8, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def container_tear_down():
    """
    Reset Container's internal type cache after each test.
    Required because the test runner truncates tables after each test, and that
    invalidates the cached container types.
    """
    yield None  # run the test
    Container.reset_cache()


########################################################################################################################
# Fixtures:


# The fixtures available below and their hierarchy are:
#
# lp (LearningPackage)
# ├─ grandparent (ContainerContainer)
# │  ├─ parent_of_two (TestContainer)
# │  │  ├─ child_entity1 (PublishableEntity)
# │  │  └─ child_entity2 (PublishableEntity)
# │  └─ parent_of_three (TestContainer)
# │     ├─ child_entity3 (📌 pinned to v1, PublishableEntity)
# │     ├─ child_entity2 (📌 pinned to v1, PublishableEntity)
# │     └─ child_entity1 (PublishableEntity)
# │
# ├─ parent_of_six (TestContainer, has duplicate children)
# │  ├─ child_entity3 (📌 pinned to v1, PublishableEntity)
# │  ├─ child_entity2 (📌 pinned to v1, PublishableEntity)
# │  ├─ child_entity1 (PublishableEntity)
# │  ├─ child_entity1 (PublishableEntity)
# │  ├─ child_entity2 (📌 pinned to v1, PublishableEntity)
# │  └─ child_entity3 (PublishableEntity)
# │
# └─ container_of_uninstalled_type ("misc" Container - it's specific type plugin no longer available)
#    └─ child_entity1 (PublishableEntity)
#
# lp2 (LearningPackage)
# └─ other_lp_parent (TestContainer)
#    └─ other_lp_child (PublishableEntity)
#
# Note that the "child" entities are referenced in multiple containers
# Everything is initially in a draft state only, with no published version.


@pytest.fixture(name="other_user")
def _other_user(django_user_model):
    return django_user_model.objects.create_user(username="other", password="something")


@pytest.fixture(name="lp")
def _lp() -> LearningPackage:
    """Get a Learning Package."""
    return publishing_api.create_learning_package(package_ref="containers-test-lp", title="Testing Containers Main LP")


@pytest.fixture(name="lp2")
def _lp2() -> LearningPackage:
    """Get a Second Learning Package."""
    return publishing_api.create_learning_package(package_ref="containers-test-lp2", title="Testing Containers (📦 2)")


def create_test_entity(learning_package: LearningPackage, ref: str, title: str) -> TestEntity:
    """Create a TestEntity with a draft version"""
    pe = publishing_api.create_publishable_entity(learning_package.id, ref, created=now, created_by=None)
    new_entity = TestEntity.objects.create(publishable_entity=pe)
    pev = publishing_api.create_publishable_entity_version(
        new_entity.pk,
        version_num=1,
        title=title,
        created=now,
        created_by=None,
    )
    TestEntityVersion.objects.create(publishable_entity_version=pev)
    return new_entity


@pytest.fixture(name="child_entity1")
def _child_entity1(lp: LearningPackage) -> TestEntity:
    """An example entity, such as a component"""
    return create_test_entity(lp, ref="child_entity1", title="Child 1 🌴")


@pytest.fixture(name="child_entity2")
def _child_entity2(lp: LearningPackage) -> TestEntity:
    """An example entity, such as a component"""
    return create_test_entity(lp, ref="child_entity2", title="Child 2 🌈")


@pytest.fixture(name="child_entity3")
def _child_entity3(lp: LearningPackage) -> TestEntity:
    """An example entity, such as a component"""
    return create_test_entity(lp, ref="child_entity3", title="Child 3 ⛵️")


@pytest.fixture(name="other_lp_child")
def _other_lp_child(lp2: LearningPackage) -> TestEntity:
    """An example entity, such as a component"""
    return create_test_entity(lp2, ref="other_lp_child", title="Child in other Learning Package 📦")


def create_test_container(
    learning_package: LearningPackage,
    container_code: str,
    entities: containers_api.EntityListInput,
    title: str = "",
) -> TestContainer:
    """Create a TestContainer with a draft version"""
    container, _version = containers_api.create_container_and_version(
        learning_package.id,
        container_code=container_code,
        title=title or f"Container ({container_code})",
        entities=entities,
        container_cls=TestContainer,
        created=now,
        created_by=None,
    )
    return container


@pytest.fixture(name="parent_of_two")
def _parent_of_two(lp: LearningPackage, child_entity1: TestEntity, child_entity2: TestEntity) -> TestContainer:
    """An TestContainer with two children"""
    return create_test_container(
        lp,
        container_code="parent_of_two",
        title="Generic Container with Two Unpinned Children",
        entities=[child_entity1, child_entity2],
    )


@pytest.fixture(name="parent_of_three")
def _parent_of_three(
    lp: LearningPackage,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
) -> TestContainer:
    """An TestContainer with three children, two of which are pinned"""
    return create_test_container(
        lp,
        container_code="parent_of_three",
        title="Generic Container with Two 📌 Pinned Children and One Unpinned",
        entities=[child_entity3.versioning.draft, child_entity2.versioning.draft, child_entity1],
    )


@pytest.fixture(name="parent_of_six")
def _parent_of_six(
    lp: LearningPackage,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
) -> TestContainer:
    """An TestContainer with six children, two of each entity, with different pinned combinations"""
    return create_test_container(
        lp,
        container_code="parent_of_six",
        title="Generic Container with Two 📌 Pinned Children and One Unpinned",
        entities=[
            # 1: both unpinned, 2: both pinned, and 3: pinned and unpinned
            child_entity3.versioning.draft,
            child_entity2.versioning.draft,
            child_entity1,
            child_entity1,
            child_entity2.versioning.draft,
            child_entity3,
        ],
    )


@pytest.fixture(name="grandparent")
def _grandparent(
    lp: LearningPackage,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
) -> ContainerContainer:
    """An ContainerContainer with two unpinned children"""
    grandparent, _version = containers_api.create_container_and_version(
        lp.id,
        container_code="grandparent",
        title="Generic Container with Two Unpinned TestContainer children",
        entities=[parent_of_two, parent_of_three],
        container_cls=ContainerContainer,
        created=now,
        created_by=None,
    )
    return grandparent


@pytest.fixture(name="container_of_uninstalled_type")
def _container_of_uninstalled_type(lp: LearningPackage, child_entity1: TestEntity) -> Container:
    """
    A container whose Container subclass implementation is no longer available,
    e.g. leftover data from an uninstalled plugin.
    """
    # First create a TestContainer, then we'll modify it to simulate it being from an uninstalled plugin
    container, _ = containers_api.create_container_and_version(
        lp.id,
        container_code="abandoned-container",
        title="Abandoned Container 1",
        entities=[child_entity1],
        container_cls=TestContainer,
        created=now,
    )
    # Now create the plugin type (no public API for this; only do this in a test)
    ctr = ContainerType.objects.create(type_code="misc")
    Container.objects.filter(pk=container.id).update(container_type=ctr)
    return Container.objects.get(pk=container.id)  # Reload and just use the base Container type


@pytest.fixture(name="other_lp_parent")
def _other_lp_parent(lp2: LearningPackage, other_lp_child: TestEntity) -> TestContainer:
    """An TestContainer with one child"""
    other_lp_parent, _version = containers_api.create_container_and_version(
        lp2.id,
        container_code="other_lp_parent",
        title="Generic Container with One Unpinned Child Entity",
        entities=[other_lp_child],
        container_cls=TestContainer,
        created=now,
        created_by=None,
    )
    return other_lp_parent


def publish_entity(obj: PublishableEntityMixin) -> PublishLog:
    """Helper method to publish a single container or other entity."""
    lp_id = obj.publishable_entity.learning_package_id
    return publishing_api.publish_from_drafts(
        lp_id,
        draft_qset=publishing_api.get_all_drafts(lp_id).filter(entity=obj.publishable_entity),
    )


def modify_entity(obj: TestEntity, title="Newly modified entity"):
    """Modify a TestEntity, creating a new version with a new title"""
    assert isinstance(obj, TestEntity)
    new_raw_version = publishing_api.create_publishable_entity_version(
        obj.pk, version_num=obj.versioning.latest.version_num + 1, title=title, created=now, created_by=None
    )
    return TestEntityVersion.objects.create(pk=new_raw_version.pk)


def Entry(
    component_version: PublishableEntityVersionMixin,
    pinned: bool = False,
) -> containers_api.ContainerEntityListEntry:
    """Helper for quickly constructing ContainerEntityListEntry entries"""
    return containers_api.ContainerEntityListEntry(component_version.publishable_entity_version, pinned=pinned)


########################################################################################################################

# `create_container`, and `create_container_version` are not tested directly here, but they are used indirectly by
# `create_container_and_version`. They are also used explicitly in `ContainerSideEffectsTestCase`, below.

# Basic tests of `create_container_and_version`


def test_create_generic_empty_container(lp: LearningPackage, admin_user) -> None:
    """
    Creating an empty TestContainer. It will have only a draft version.
    """
    container, container_v1 = containers_api.create_container_and_version(
        lp.id,
        container_code="new-container-1",
        title="Test Container 1",
        container_cls=TestContainer,
        created=now,
        created_by=admin_user.pk,
        can_stand_alone=False,
    )

    if TYPE_CHECKING:
        # `create_container_and_version()` should return the correct Container subclass, based on `container_cls=...`:
        assert_type(container, TestContainer)

        # Documenting status quo. We want `container.pk` to have type 'Container.ID', but we cannot do that because of
        # how django-stubs overrides 'pk' when a model uses a OneToOneField as primary key. However, you should see that
        # `pk` below is crossed out (if you use an IDE), to remind you to use 'id' instead.
        assert_type(container.pk, Any)

        # We use this to get the primary key instead. Properly typed.
        assert_type(container.id, Container.ID)

        # As of Python 3.12, it's not yet possible to make the the returned version (`container_v1`) use a type that is
        # computed based on `container_cls`. Ideally this would have type `TestContainerVersion`, not
        # `ContainerVersion`.
        # Revisit as we upgrade to later python versions, perhaps with something like PEP 827.
        # assert_type(container_v1, TestContainerVersion)

    # Note the assert_type() calls must come before 'assert isinstance()' or they'll have no effect.
    assert isinstance(container, TestContainer)
    assert isinstance(container_v1, TestContainerVersion)
    assert container.versioning.draft == container_v1
    assert container.versioning.published is None
    assert container.entity_ref == "new-container-1"
    assert container.versioning.draft.title == "Test Container 1"
    assert container.created == now
    assert container.created_by == admin_user
    assert container.versioning.draft.created == now
    assert container.versioning.draft.created_by == admin_user
    assert not container.can_stand_alone

    assert containers_api.get_container_children_count(container, published=False) == 0
    with pytest.raises(ContainerVersion.DoesNotExist):
        containers_api.get_container_children_count(container, published=True)


def test_unicode_code(lp: LearningPackage) -> None:
    """container_code supports non-ascii letters."""
    unicode_code = "柏倉隆史"
    container, _ = containers_api.create_container_and_version(
        lp.id,
        container_code=unicode_code,
        title="Unicode Container",
        container_cls=TestContainer,
        created=now,
        created_by=None,
    )
    assert container.container_code == unicode_code
    assert containers_api.get_container_by_code(lp.id, unicode_code).id == container.id


def test_create_container_queries(lp: LearningPackage, child_entity1: TestEntity, django_assert_num_queries) -> None:
    """Test how many database queries are required to create a container."""
    base_args: dict[str, Any] = {
        "title": "Test Container",
        "created": now,
        "created_by": None,
        "container_cls": TestContainer,
    }
    # The exact numbers here aren't too important - this is just to alert us if anything significant changes.
    with django_assert_num_queries(34):
        containers_api.create_container_and_version(lp.id, container_code="c1", **base_args)
    # And try with a a container that has children:
    with django_assert_num_queries(37):
        containers_api.create_container_and_version(lp.id, container_code="c2", **base_args, entities=[child_entity1])


# versioning helpers


def test_container_versioning_helpers(parent_of_two: TestContainer):
    """
    Test that the .versioning helper of a subclass like `TestContainer` returns a `TestContainerVersion`, and
    same for the base class `Container` equivalent.
    """
    assert isinstance(parent_of_two, TestContainer)
    base_container = parent_of_two.container
    assert base_container.__class__ is Container
    container_version = base_container.versioning.draft
    assert container_version.__class__ is ContainerVersion
    subclass_version = parent_of_two.versioning.draft
    assert isinstance(subclass_version, TestContainerVersion)
    assert subclass_version.container_version == container_version
    assert subclass_version.container_version.container == base_container
    assert subclass_version.container_version.container.testcontainer == parent_of_two


# create_next_container_version


def test_create_next_container_version_no_changes(parent_of_two: TestContainer, other_user):
    """
    Test creating a new version of the "parent of two" container, but without
    any actual changes.
    """
    original_version = parent_of_two.versioning.draft
    assert original_version.version_num == 1

    # Create a new version with no changes:
    v2_date = datetime.now(tz=timezone.utc)
    version_2 = containers_api.create_next_container_version(
        parent_of_two,
        created=v2_date,
        created_by=other_user.pk,
        # Specify no changes at all
    )

    # assert_type(version_2, TestContainerVersion)
    # ^ Must come before 'assert isinstance(...)'. Unfortunately, getting the subclass return type in python 3.12 is not
    # possible unless we explicitly pass in the ContainerVersion subclass as a parameter (which makes the API less
    # generic) or convert `create_next_container_version()` to a classmethod on Container, which is inconsistent with
    # our convention of a function-based public API and semi-private model API.
    assert isinstance(version_2, TestContainerVersion)

    # Now it should have an incremented version number but be unchanged:
    assert version_2 == parent_of_two.versioning.draft
    assert version_2.version_num == 2
    assert version_2.title == original_version.title
    # Since we didn't change the entities, the same entity list should be re-used:
    assert version_2.entity_list_id == original_version.entity_list_id
    assert version_2.created == v2_date
    assert version_2.created_by == other_user
    assert containers_api.get_container_children_entity_refs(
        original_version
    ) == containers_api.get_container_children_entity_refs(version_2)


def test_create_next_container_version_with_changes(
    parent_of_two: TestContainer, child_entity1: TestEntity, child_entity2: TestEntity
):
    """
    Test creating a new version of the "parent of two" container, changing the
    title and swapping the order of the children
    """
    original_version = parent_of_two.versioning.draft
    assert original_version.version_num == 1

    # Create a new version, specifying version number 5 and changing the title and the order of the children:
    v5_date = datetime.now(tz=timezone.utc)
    containers_api.create_next_container_version(
        parent_of_two,
        title="New Title - children reversed",
        entities=[child_entity2, child_entity1],  # Reversed from original [child_entity1, child_entity2] order
        force_version_num=5,
        created=v5_date,
        created_by=None,
    )

    # Now retrieve the new version:
    version_5 = parent_of_two.versioning.draft
    assert parent_of_two.versioning.published is None  # No change to published version
    assert version_5.version_num == 5
    assert version_5.created == v5_date
    assert version_5.created_by is None
    assert version_5.title == "New Title - children reversed"
    assert version_5.entity_list_id != original_version.entity_list_id
    assert containers_api.get_container_children_entity_refs(version_5) == ["child_entity2", "child_entity1"]


def test_create_next_container_version_with_append(
    parent_of_two: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test creating a new version of the "parent of two" container, using the APPEND action to append new children.
    """
    original_version = parent_of_two.versioning.draft
    assert original_version.version_num == 1
    child_entity1_v1 = child_entity1.versioning.draft
    assert child_entity1_v1.version_num == 1

    # Create a new version, APPENDing entity 3 and 📌 pinned entity1 (v1)
    version_2 = containers_api.create_next_container_version(
        parent_of_two,
        entities=[child_entity3, child_entity1_v1],
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.APPEND,
    )

    assert parent_of_two.versioning.draft == version_2
    assert containers_api.get_entities_in_container(parent_of_two, published=False) == [
        Entry(child_entity1.versioning.draft, pinned=False),  # Unchanged, original first child
        Entry(child_entity2.versioning.draft, pinned=False),  # Unchanged, original second child
        Entry(child_entity3.versioning.draft, pinned=False),  # 🆕 entity 3, appended, unpinned
        Entry(child_entity1_v1, pinned=True),  # 🆕 entity 1, appended, 📌 pinned
    ]


def test_create_next_container_version_with_remove_1(
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test creating a new version of the "parent of six" container, using the REMOVE action to remove children.
    """
    # Before looks like this:
    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]
    # Remove "entity 1 unpinned" - should remove both:
    containers_api.create_next_container_version(
        parent_of_six.id,
        entities=[child_entity1],
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.REMOVE,
    )
    # Now it looks like:

    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        # entity 1 unpinned x2 removed
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]


def test_create_next_container_version_with_remove_2(
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test creating a new version of the "parent of six" container, using the REMOVE action to remove children.
    """
    # Before looks like this:
    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]
    # Remove "entity 2 pinned" - should remove both:
    containers_api.create_next_container_version(
        parent_of_six.id,
        entities=[child_entity2.versioning.draft],  # specify the version for "pinned"
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.REMOVE,
    )
    # Now it looks like:

    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        # removed
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        # removed
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]


def test_create_next_container_version_with_remove_3(
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test creating a new version of the "parent of six" container, using the REMOVE action to remove children.
    """
    # Before looks like this:
    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]
    # Remove "entity 3 pinned" - should remove only one:
    containers_api.create_next_container_version(
        parent_of_six.id,
        entities=[child_entity3.versioning.draft],  # specify the version for "pinned"
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.REMOVE,
    )
    # Now it looks like:

    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        # entity 3 pinned removed
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned so should not be removed
    ]


def test_create_next_container_version_with_remove_4(
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test creating a new version of the "parent of six" container, using the REMOVE action to remove children.
    """
    # Before looks like this:
    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity3.versioning.draft, pinned=False),  # entity 3, unpinned
    ]
    # Remove "entity 3 unpinned" - should remove only one:
    containers_api.create_next_container_version(
        parent_of_six.id,
        entities=[child_entity3],
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.REMOVE,
    )
    # Now it looks like:

    assert containers_api.get_entities_in_container(parent_of_six, published=False) == [
        Entry(child_entity3.versioning.draft, pinned=True),  # entity 3, 📌 pinned so should not be removed
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity1.versioning.draft, pinned=False),  # entity 1, unpinned
        Entry(child_entity2.versioning.draft, pinned=True),  # entity 2, 📌 pinned
        # entity 3 unpinned removed
    ]


def test_create_next_container_version_with_conflicting_version(parent_of_two: TestContainer):
    """
    Test that an appropriate error is raised when calling `create_next_container_version` and specifying a version
    number that already exists.
    """

    def create_v5():
        """Create a new version, specifying version number 5 and changing the title and the order of the children."""
        containers_api.create_next_container_version(
            parent_of_two.id,
            title="New version - forced as v5",
            force_version_num=5,
            created=now,
            created_by=None,
        )

    # First it should work:
    create_v5()
    # Then it should fail:
    with pytest.raises(IntegrityError):
        create_v5()


def test_create_next_container_version_uninstalled_plugin(container_of_uninstalled_type: Container):
    """
    Test that an appropriate error is raised when calling `create_next_container_version` for a container whose type
    implementation is no longer installed. Such containers should still be readable but not writable.
    """
    with pytest.raises(containers_api.ContainerImplementationMissingError):
        containers_api.create_next_container_version(
            container_of_uninstalled_type.id,
            title="New version of the container",
            created=now,
            created_by=None,
        )


def test_create_next_container_version_other_lp(parent_of_two: TestContainer, other_lp_child: PublishableEntity):
    """
    Test that an appropriate error is raised when trying to add a child from another learning package to a container.
    """
    with pytest.raises(ValidationError, match="Container entities must be from the same learning package."):
        containers_api.create_next_container_version(
            parent_of_two.id,
            title="Bad Version with entities from another learning package",
            created=now,
            created_by=None,
            entities=[other_lp_child],  # <-- from "lp2" Learning Package
        )


# get_container


def test_get_container(parent_of_two: TestContainer, django_assert_num_queries) -> None:
    """
    Test `get_container()`
    """
    with django_assert_num_queries(1):
        result = containers_api.get_container(parent_of_two.id)
    assert result == parent_of_two.container
    # Versioning data should be pre-loaded via the default select_related() of Container.objects used by get_container
    with django_assert_num_queries(0):
        assert result.versioning.has_unpublished_changes


def test_get_container_nonexistent() -> None:
    """
    Test `get_container()` with an invalid ID.
    """
    FAKE_ID = cast(Container.ID, -500)
    with pytest.raises(Container.DoesNotExist):
        containers_api.get_container(FAKE_ID)


def test_get_container_soft_deleted(parent_of_two: TestContainer) -> None:
    """
    Test `get_container()` with a soft deleted container
    """
    publishing_api.soft_delete_draft(parent_of_two.id, deleted_by=None)
    parent_of_two.refresh_from_db()
    assert parent_of_two.versioning.draft is None
    assert parent_of_two.versioning.published is None
    # Get the container
    result = containers_api.get_container(parent_of_two.id)
    assert result == parent_of_two.container  # It works fine! get_container() ignores publish/delete status.


def test_get_container_uninstalled_type(container_of_uninstalled_type: Container) -> None:
    """
    Test `get_container()` with a container from an uninstalled plugin
    """
    # Nothing special happens. It should work fine.
    result = containers_api.get_container(container_of_uninstalled_type.id)
    assert result == container_of_uninstalled_type


# get_container_version


def test_get_container_version(parent_of_two: TestContainer) -> None:
    """
    Test getting a specific container version
    """
    # Note: This is not a super useful API, and we're not using it anywhere.
    cv = containers_api.get_container_version(parent_of_two.versioning.draft.pk)
    assert cv == parent_of_two.versioning.draft.container_version


def test_get_container_version_nonexistent() -> None:
    """
    Test getting a specific container version that doesn't exist
    """
    with pytest.raises(ContainerVersion.DoesNotExist):
        containers_api.get_container_version(-500)


# get_container_by_code


def test_get_container_by_code(lp: LearningPackage, parent_of_two: TestContainer) -> None:
    """
    Test getting a specific container by container code.
    """
    result = containers_api.get_container_by_code(lp.id, parent_of_two.container_code)
    assert result == parent_of_two.container
    # The API always returns "Container", not specific subclasses like TestContainer:
    assert result.__class__ is Container


def test_get_container_by_code_nonexistent(lp: LearningPackage) -> None:
    """
    Test getting a specific container by container code, where the code and/or learning package is invalid.
    """
    FAKE_ID = cast(LearningPackage.ID, -500)
    with pytest.raises(LearningPackage.DoesNotExist):
        containers_api.get_container_by_code(FAKE_ID, "invalid-code")

    with pytest.raises(Container.DoesNotExist):
        containers_api.get_container_by_code(lp.id, "invalid-code")


# get_container_subclass


def test_get_container_subclass() -> None:
    """
    Test get_container_subclass()
    """
    assert containers_api.get_container_subclass("test_generic") is TestContainer
    assert containers_api.get_container_subclass("test_container_container") is ContainerContainer
    with pytest.raises(
        containers_api.ContainerImplementationMissingError,
        match='An implementation for "foo" containers is not currently installed.',
    ):
        containers_api.get_container_subclass("foo")


# get_all_container_subclasses
def test_get_all_container_subclasses() -> None:
    """
    Test get_all_container_subclasses()
    """
    # For test purposes, filter the list to only include containers from our "test_django_app":
    assert [ct for ct in containers_api.get_all_container_subclasses() if ct._meta.app_label == "test_django_app"] == [
        ContainerContainer,
        TestContainer,
    ]


# get_container_type_code_of and get_container_subclass_of


def test_get_container_subclass_of(
    grandparent: ContainerContainer, parent_of_two: TestContainer, child_entity1: TestEntity
):
    """
    Test get_container_type_code_of() and get_container_subclass_of()
    """
    # Grandparent is a "ContainerContainer":
    assert isinstance(grandparent, ContainerContainer)
    assert containers_api.get_container_type_code_of(grandparent) == "test_container_container"
    assert containers_api.get_container_subclass_of(grandparent) is ContainerContainer
    # The functions work even if we pass a generic "Container" object:
    assert isinstance(grandparent.base_container, Container)
    assert containers_api.get_container_type_code_of(grandparent.base_container) == "test_container_container"
    assert containers_api.get_container_subclass_of(grandparent.base_container) is ContainerContainer

    # "Parent of Two" is a "TestContainer":
    assert isinstance(parent_of_two, TestContainer)
    assert containers_api.get_container_type_code_of(parent_of_two) == "test_generic"
    assert containers_api.get_container_subclass_of(parent_of_two) is TestContainer
    assert isinstance(parent_of_two.container, Container)
    assert containers_api.get_container_type_code_of(parent_of_two.container) == "test_generic"
    assert containers_api.get_container_subclass_of(parent_of_two.container) is TestContainer

    # Passing in a non-container will trigger an assert failure:
    with pytest.raises(AssertionError):
        containers_api.get_container_subclass_of(child_entity1)  # type: ignore


def test_get_container_type_deleted(container_of_uninstalled_type: Container):
    """
    `get_container_subclass_of` will raise ValueError if the container type implementation is no longer available
    """
    with pytest.raises(
        containers_api.ContainerImplementationMissingError,
        match='An implementation for "misc" containers is not currently installed.',
    ):
        containers_api.get_container_subclass_of(container_of_uninstalled_type)

    # But get_container_type_code() should still work:
    assert containers_api.get_container_type_code_of(container_of_uninstalled_type) == "misc"


# get_containers


def test_get_containers(
    lp: LearningPackage,
    grandparent: ContainerContainer,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
    lp2: LearningPackage,
    other_lp_parent: TestContainer,
):
    """
    Test that we can get all containers in a Learning Package
    """
    result = list(containers_api.get_containers(lp.id))
    # The API always returns Container base class instances, never specific types:
    assert all(c.__class__ is Container for c in result)
    # (we _could_ implement a get_typed_containers() API, but there's probably no need?)
    assert result == [
        # Default ordering is in the order they were created:
        parent_of_two.container,
        parent_of_three.container,
        grandparent.base_container,
    ]
    # Now repeat with the other Learning Package, to make sure they're isolated:
    assert list(containers_api.get_containers(lp2.id)) == [
        other_lp_parent.container,
    ]


def test_get_containers_soft_deleted(
    lp: LearningPackage,
    grandparent: ContainerContainer,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
):
    """
    Test that soft deleted containers are excluded from `get_containers()` by
    default, but can be included.
    """
    # Soft delete `parent_of_two`:
    publishing_api.soft_delete_draft(parent_of_two.id)
    # Now it should not be included in the result:
    assert list(containers_api.get_containers(lp.id)) == [
        # parent_of_two is not returned.
        parent_of_three.container,
        grandparent.base_container,
    ]
    # Unless we specify include_deleted=True:
    assert list(containers_api.get_containers(lp.id, include_deleted=True)) == [
        parent_of_two.container,
        parent_of_three.container,
        grandparent.base_container,
    ]


# General publishing tests.


def test_contains_unpublished_changes_queries(
    grandparent: ContainerContainer, child_entity1: TestEntity, django_assert_num_queries
) -> None:
    """Test that `contains_unpublished_changes()` works, and check how many queries it uses"""
    # Setup: grandparent and all its descendants are unpublished drafts only.
    assert grandparent.versioning.published is None

    # Tests:
    with django_assert_num_queries(1):
        assert containers_api.contains_unpublished_changes(grandparent)
    with django_assert_num_queries(1):
        assert containers_api.contains_unpublished_changes(grandparent.id)

    # Publish grandparent and all its descendants:
    with django_assert_num_queries(20):
        publish_entity(grandparent)

    # Tests:
    with django_assert_num_queries(1):
        assert not containers_api.contains_unpublished_changes(grandparent)

    # Now make a tiny change to a grandchild component (not a direct child of "grandparent"), and make sure it's
    # detected:
    publishing_api.create_publishable_entity_version(
        child_entity1.pk,
        version_num=2,
        title="Modified grandchild",
        created=now,
        created_by=None,
    )
    child_entity1.refresh_from_db()
    assert child_entity1.versioning.has_unpublished_changes

    with django_assert_num_queries(1):
        assert containers_api.contains_unpublished_changes(grandparent)


def test_auto_publish_children(
    parent_of_two: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    Test that publishing a container publishes its child components automatically.
    """
    # At first, nothing is published:
    assert containers_api.contains_unpublished_changes(parent_of_two.id)
    assert child_entity1.versioning.published is None
    assert child_entity2.versioning.published is None
    assert child_entity3.versioning.published is None
    child_entity1_v1 = child_entity1.versioning.draft

    # Publish ONLY the "parent_of_two" container.
    # This should however also auto-publish components 1 & 2 since they're children
    publish_entity(parent_of_two)
    # Now all changes to the container and its two children are published:
    for entity in [parent_of_two, child_entity1, child_entity2, child_entity3]:
        entity.refresh_from_db()
    assert parent_of_two.versioning.has_unpublished_changes is False  # Shallow check
    assert child_entity1.versioning.has_unpublished_changes is False
    assert child_entity2.versioning.has_unpublished_changes is False
    assert containers_api.contains_unpublished_changes(parent_of_two.id) is False  # Deep check
    assert child_entity1.versioning.published == child_entity1_v1  # v1 is now the published version.

    # But our other component that's outside the container is not affected:
    child_entity3.refresh_from_db()
    assert child_entity3.versioning.has_unpublished_changes
    assert child_entity3.versioning.published is None


def test_no_publish_parent(parent_of_two: TestContainer, child_entity1: TestEntity):
    """
    Test that publishing an entity does NOT publish changes to its parent containers
    """
    # "child_entity1" is a child of "parent_of_two"
    children_refs = containers_api.get_container_children_entity_refs(parent_of_two.versioning.draft)
    assert child_entity1.entity_ref in children_refs
    # Neither are published:
    assert child_entity1.versioning.published is None
    assert parent_of_two.versioning.published is None

    # Publish ONLY one of its child components
    publish_entity(child_entity1)
    child_entity1.refresh_from_db()  # Clear cache on '.versioning'
    assert child_entity1.versioning.has_unpublished_changes is False

    # The container that contains that component should still be unpublished:
    parent_of_two.refresh_from_db()  # Clear cache on '.versioning'
    assert parent_of_two.versioning.has_unpublished_changes
    assert parent_of_two.versioning.published is None
    with pytest.raises(ContainerVersion.DoesNotExist):
        # There is no published version of "parent_of_two":
        containers_api.get_entities_in_container(parent_of_two, published=True)


def test_add_entity_after_publish(lp: LearningPackage, parent_of_two: TestContainer, child_entity3: TestEntity):
    """
    Adding an entity to a published container will create a new version and show that the container has unpublished
    changes.
    """
    parent_of_two_v1 = parent_of_two.versioning.draft
    assert parent_of_two_v1.version_num == 1
    assert parent_of_two.versioning.published is None
    # Publish everything in the learning package:
    publishing_api.publish_all_drafts(lp.id)
    parent_of_two.refresh_from_db()  # Reloading is necessary
    assert not parent_of_two.versioning.has_unpublished_changes  # Shallow check
    assert not containers_api.contains_unpublished_changes(parent_of_two)  # Deeper check

    # Add a published entity (child_entity3, unpinned):
    parent_of_two_v2 = containers_api.create_next_container_version(
        parent_of_two.id,
        entities=[child_entity3],
        created=now,
        created_by=None,
        entities_action=containers_api.ChildrenEntitiesAction.APPEND,
    )
    # Now the container should have unpublished changes:
    parent_of_two.refresh_from_db()  # Reloading the container is necessary
    assert parent_of_two.versioning.has_unpublished_changes  # Shallow check - adding a child changes the container
    assert containers_api.contains_unpublished_changes(parent_of_two)  # Deeper check
    assert parent_of_two.versioning.draft == parent_of_two_v2
    assert parent_of_two.versioning.published == parent_of_two_v1


def test_modify_unpinned_entity_after_publish(
    parent_of_two: TestContainer, child_entity1: TestEntity, child_entity2: TestEntity
):
    """
    Modifying an unpinned entity in a published container will NOT create a new version nor show that the container has
    unpublished changes (but it will "contain" unpublished changes). The modifications will appear in the published
    version of the container only after the child entity is published.
    """
    # Use "parent_of_two" which has two unpinned child entities.
    # Publish it and its two children:
    publish_entity(parent_of_two)
    parent_of_two.refresh_from_db()  # Technically reloading is only needed if we accessed 'versioning' before publish
    child_entity1_v1 = child_entity1.versioning.draft
    child_entity2_v1 = child_entity2.versioning.draft

    assert parent_of_two.versioning.has_unpublished_changes is False  # Shallow check
    assert containers_api.contains_unpublished_changes(parent_of_two.id) is False  # Deeper check
    assert child_entity1.versioning.has_unpublished_changes is False

    # Now modify the child entity (it remains a draft):
    child_entity1_v2 = modify_entity(child_entity1)

    # The component now has unpublished changes; the container doesn't directly but does contain
    parent_of_two.refresh_from_db()  # Reloading the container is necessary, or '.versioning' will be outdated
    child_entity1.refresh_from_db()
    assert (
        parent_of_two.versioning.has_unpublished_changes is False
    )  # Shallow check should be false - container is unchanged
    assert containers_api.contains_unpublished_changes(
        parent_of_two.id
    )  # But the container DOES "contain" changes
    assert child_entity1.versioning.has_unpublished_changes

    # Since the child's changes haven't been published, they should only appear in the draft container
    assert containers_api.get_entities_in_container(parent_of_two, published=False) == [
        Entry(child_entity1_v2),  # new version
        Entry(child_entity2_v1),  # unchanged second child
    ]
    assert containers_api.get_entities_in_container(parent_of_two, published=True) == [
        Entry(child_entity1_v1),  # old version
        Entry(child_entity2_v1),  # unchanged second child
    ]

    # But if we publish the child, the changes will appear in the published version of the container.
    publish_entity(child_entity1)
    assert containers_api.get_entities_in_container(parent_of_two, published=False) == [
        Entry(child_entity1_v2),  # new version
        Entry(child_entity2_v1),  # unchanged second child
    ]
    assert containers_api.get_entities_in_container(parent_of_two, published=True) == [
        Entry(child_entity1_v2),  # new version
        Entry(child_entity2_v1),  # unchanged second child
    ]
    assert containers_api.contains_unpublished_changes(parent_of_two) is False  # No longer contains unpublished changes


def test_modify_pinned_entity(
    lp: LearningPackage,
    parent_of_three: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
):
    """
    When a pinned 📌 entity in a container is modified and/or published, it will have no effect on either the draft nor
    published version of the container, which will continue to use the pinned version.
    """
    # Note: "parent_of_three" has two pinned children and one unpinned
    expected_contents = [
        Entry(child_entity3.versioning.draft, pinned=True),  # pinned 📌 to v1
        Entry(child_entity2.versioning.draft, pinned=True),  # pinned 📌 to v1
        Entry(child_entity1.versioning.draft, pinned=False),
    ]
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == expected_contents

    # Publish everything
    publishing_api.publish_all_drafts(lp.id)

    # Now modify the first 📌 pinned child entity (#3) by changing its title (it remains a draft):
    modify_entity(child_entity3)

    # The component now has unpublished changes; the container is entirely unaffected
    parent_of_three.refresh_from_db()  # Reloading the container is necessary, or '.versioning' will be outdated
    child_entity3.refresh_from_db()
    assert parent_of_three.versioning.has_unpublished_changes is False  # Shallow check
    assert containers_api.contains_unpublished_changes(parent_of_three) is False  # Deep check
    assert child_entity3.versioning.has_unpublished_changes is True

    # Neither the draft nor the published version of the container is affected
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == expected_contents
    assert containers_api.get_entities_in_container(parent_of_three, published=True) == expected_contents
    # Even if we publish the component, the container stays pinned to the specified version:
    publish_entity(child_entity3)
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == expected_contents
    assert containers_api.get_entities_in_container(parent_of_three, published=True) == expected_contents


def test_publishing_shared_component(lp: LearningPackage):
    """
    A complex test case involving two units with a shared component and other non-shared components.

    Note these are not actual "Unit"s nor "Components" but instead `TestContainer` and `TestEntity` standing
    in for them.

    Unit 1: components C1, C2, C3
    Unit 2: components C2, C4, C5
    Everything is "unpinned".
    """
    # 1️⃣ Create the units and publish them:
    c1, c2, c3, c4, c5 = [create_test_entity(lp, ref=f"C{i}", title=f"Component {i}") for i in range(1, 6)]
    c1_v1 = c1.versioning.draft
    c3_v1 = c3.versioning.draft
    c4_v1 = c4.versioning.draft
    c5_v1 = c5.versioning.draft
    unit1, _ = containers_api.create_container_and_version(
        lp.id,
        entities=[c1, c2, c3],
        title="Unit 1",
        container_code="unit-1",
        created=now,
        created_by=None,
        container_cls=TestContainer,
    )
    unit2, _ = containers_api.create_container_and_version(
        lp.id,
        entities=[c2, c4, c5],
        title="Unit 2",
        container_code="unit-2",
        created=now,
        created_by=None,
        container_cls=TestContainer,
    )
    publishing_api.publish_all_drafts(lp.id)
    assert containers_api.contains_unpublished_changes(unit1.id) is False
    assert containers_api.contains_unpublished_changes(unit2.id) is False

    # 2️⃣ Then the author edits C2 inside of Unit 1 making C2v2.
    c2_v2 = modify_entity(c2)
    # This makes U1 and U2 both show up as Units that CONTAIN unpublished changes, because they share the component.
    assert containers_api.contains_unpublished_changes(unit1.id)
    assert containers_api.contains_unpublished_changes(unit2.id)
    # (But the units themselves are unchanged:)
    unit1.refresh_from_db()
    unit2.refresh_from_db()
    assert unit1.versioning.has_unpublished_changes is False
    assert unit2.versioning.has_unpublished_changes is False

    # 3️⃣ In addition to this, the author also modifies another component in Unit 2 (C5)
    c5_v2 = modify_entity(c5)

    # 4️⃣ The author then publishes Unit 1, and therefore everything in it.
    publish_entity(unit1)

    # Result: Unit 1 will show the newly published version of C2:
    assert containers_api.get_entities_in_container(unit1, published=True) == [
        Entry(c1_v1),
        Entry(c2_v2),  # new published version of C2
        Entry(c3_v1),
    ]

    # Result: someone looking at Unit 2 should see the newly published component 2, because publishing it anywhere
    # publishes it everywhere. But publishing C2 and Unit 1 does not affect the other components in Unit 2.
    # (Publish propagates downward, not upward)
    assert containers_api.get_entities_in_container(unit2, published=True) == [
        Entry(c2_v2),  # new published version of C2
        Entry(c4_v1),  # still original version of C4 (it was never modified)
        Entry(c5_v1),  # still original version of C5 (it hasn't been published)
    ]

    # Result: Unit 2 CONTAINS unpublished changes because of the modified C5. Unit 1 doesn't contain unpub changes.
    assert containers_api.contains_unpublished_changes(unit1.id) is False
    assert containers_api.contains_unpublished_changes(unit2.id)

    # 5️⃣ Publish component C5, which should be the only thing unpublished in the learning package
    publish_entity(c5)
    # Result: Unit 2 shows the new version of C5 and no longer contains unpublished changes:
    assert containers_api.get_entities_in_container(unit2, published=True) == [
        Entry(c2_v2),  # new published version of C2
        Entry(c4_v1),  # still original version of C4 (it was never modified)
        Entry(c5_v2),  # new published version of C5
    ]
    assert containers_api.contains_unpublished_changes(unit2.id) is False


def test_shallow_publish_log(
    lp: LearningPackage,
    grandparent: ContainerContainer,  # Create grandparent so it exists during this test; it should be untouched.
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
) -> None:
    """Simple test of publishing a container plus children and reviewing the publish log"""
    publish_log = publish_entity(parent_of_two)
    assert list(publish_log.records.order_by("entity__pk").values_list("entity__entity_ref", flat=True)) == [
        # The container and its two children should be the only things published:
        "child_entity1",
        "child_entity2",
        "parent_of_two",
    ]


def test_uninstalled_publish(
    lp: LearningPackage,
    container_of_uninstalled_type: Container,
    django_assert_num_queries,
) -> None:
    """Simple test of publishing a container of uninstalled type, plus its child, and reviewing the publish log"""
    # Publish container_of_uninstalled_type (and child_entity1). Should not affect anything else,
    # but we should see "child_entity1" omitted from the subsequent publish.
    with django_assert_num_queries(20):
        publish_log = publish_entity(container_of_uninstalled_type)
        # Nothing else should have been affected by the publish:
        assert list(publish_log.records.order_by("entity__pk").values_list("entity__entity_ref", flat=True)) == [
            "child_entity1",
            "abandoned-container",
        ]


def test_deep_publish_log(
    lp: LearningPackage,
    grandparent: ContainerContainer,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
    container_of_uninstalled_type: Container,
    lp2: LearningPackage,
    other_lp_parent: TestContainer,
    other_lp_child: TestEntity,
    django_assert_num_queries,
) -> None:
    """
    With lots of entities present in a deep hierarchy, test the result of publishing different parts of the tree.

    See diagram near the top of this file.
    """
    # Create a "great grandparent" container that contains "grandparent"
    great_grandparent = create_test_container(
        lp,
        container_code="great_grandparent",
        title="Great-grandparent container",
        entities=[grandparent],
    )
    # Publish container_of_uninstalled_type (and child_entity1). Should not affect anything else,
    # but we should see "child_entity1" omitted from the subsequent publish.
    with django_assert_num_queries(20):
        publish_log = publish_entity(container_of_uninstalled_type)
        # Nothing else should have been affected by the publish:
        assert list(publish_log.records.order_by("entity__pk").values_list("entity__entity_ref", flat=True)) == [
            "child_entity1",
            "abandoned-container",
        ]

    # Publish great_grandparent. Should publish the whole tree.
    with django_assert_num_queries(22):
        publish_log = publish_entity(great_grandparent)
        assert list(publish_log.records.order_by("entity__pk").values_list("entity__entity_ref", flat=True)) == [
            "child_entity2",
            "parent_of_two",
            "parent_of_three",
            "grandparent",
            "great_grandparent",
        ]


# get_entities_in_container


def test_get_entities_in_container(
    parent_of_three: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
) -> None:
    """
    Test `get_entities_in_container()`
    """
    expected = [
        # This particular container has three children (3, 2, 1), two of them 📌 pinned:
        containers_api.ContainerEntityListEntry(child_entity3.versioning.draft.publishable_entity_version, pinned=True),
        containers_api.ContainerEntityListEntry(child_entity2.versioning.draft.publishable_entity_version, pinned=True),
        containers_api.ContainerEntityListEntry(
            child_entity1.versioning.draft.publishable_entity_version, pinned=False
        ),
    ]
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == expected
    # Asking about the published version will throw an exception, since no published version exists yet:
    with pytest.raises(ContainerVersion.DoesNotExist):
        containers_api.get_entities_in_container(parent_of_three, published=True)

    publish_entity(parent_of_three)
    assert containers_api.get_entities_in_container(parent_of_three, published=True) == expected


def test_get_entities_in_container_soft_deletion_unpinned(
    parent_of_three: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
) -> None:
    """Test that `get_entities_in_container()` correctly handles soft deletion of child entities."""
    before = [  # This particular container has three children (3, 2, 1), two of them 📌 pinned:
        Entry(child_entity3.versioning.draft, pinned=True),
        Entry(child_entity2.versioning.draft, pinned=True),
        Entry(child_entity1.versioning.draft, pinned=False),
    ]
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == before

    # First, publish everything:
    publish_entity(parent_of_three)
    # Soft delete the third, unpinned child (child_entity1):
    publishing_api.soft_delete_draft(child_entity1.pk)

    # That deletion should NOT count as a change to the container itself:
    parent_of_three.refresh_from_db()
    assert not parent_of_three.versioning.has_unpublished_changes
    # But it "contains" a change (a deletion)
    assert containers_api.contains_unpublished_changes(parent_of_three)

    after = [
        before[0],  # first two children are unchanged
        before[1],
        # the third child (#1) has been soft deleted and doesn't appear in the draft
    ]
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == after


def test_get_entities_in_container_soft_deletion_pinned(
    parent_of_three: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
) -> None:
    """Test that `get_entities_in_container()` correctly handles soft deletion of 📌 pinned child entities."""
    before = [  # This particular container has three children (3, 2, 1), two of them 📌 pinned:
        Entry(child_entity3.versioning.draft, pinned=True),
        Entry(child_entity2.versioning.draft, pinned=True),
        Entry(child_entity1.versioning.draft, pinned=False),
    ]
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == before

    # First, publish everything:
    publish_entity(parent_of_three)
    # Soft delete child 2:
    publishing_api.soft_delete_draft(child_entity2.pk)

    # The above deletions should NOT count as a change to the container itself, in any way:
    parent_of_three.refresh_from_db()
    assert not parent_of_three.versioning.has_unpublished_changes
    assert not containers_api.contains_unpublished_changes(parent_of_three)

    # Since the second child was pinned to an exact version, soft deleting it doesn't affect the contents of the
    # container at all:
    assert containers_api.get_entities_in_container(parent_of_three, published=False) == before


# get_entities_in_container_as_of


def test_snapshots_of_published_unit(lp: LearningPackage, child_entity1: TestEntity, child_entity2: TestEntity):
    """Test that we can access snapshots of the historic published version of containers and their contents."""
    child_entity1_v1 = child_entity1.versioning.draft

    # At first the container has one child (unpinned):
    container = create_test_container(lp, container_code="c", entities=[child_entity1])
    modify_entity(child_entity1, title="Component 1 as of checkpoint 1")
    _, before_publish = containers_api.get_entities_in_container_as_of(container, 0)
    assert not before_publish  # Empty list

    # Publish everything, creating Checkpoint 1
    checkpoint_1 = publishing_api.publish_all_drafts(lp.id, message="checkpoint 1")

    ########################################################################

    # Now we update the title of the component.
    modify_entity(child_entity1, title="Component 1 as of checkpoint 2")
    # Publish everything, creating Checkpoint 2
    checkpoint_2 = publishing_api.publish_all_drafts(lp.id, message="checkpoint 2")
    ########################################################################

    # Now add a second component to the unit:
    modify_entity(child_entity1, title="Component 1 as of checkpoint 3")
    modify_entity(child_entity2, title="Component 2 as of checkpoint 3")
    containers_api.create_next_container_version(
        container.id,
        title="Unit title in checkpoint 3",
        entities=[child_entity1, child_entity2],
        created=now,
        created_by=None,
    )
    # Publish everything, creating Checkpoint 3
    checkpoint_3 = publishing_api.publish_all_drafts(lp.id, message="checkpoint 3")
    ########################################################################

    # Now add a third component to the unit, a pinned 📌 version of component 1.
    # This will test pinned versions and also test adding at the beginning rather than the end of the unit.
    containers_api.create_next_container_version(
        container.id,
        title="Unit title in checkpoint 4",
        entities=[child_entity1_v1, child_entity1, child_entity2],
        created=now,
        created_by=None,
    )
    # Publish everything, creating Checkpoint 4
    checkpoint_4 = publishing_api.publish_all_drafts(lp.id, message="checkpoint 4")
    ########################################################################

    # Modify the drafts, but don't publish:
    modify_entity(child_entity1, title="Component 1 draft")
    modify_entity(child_entity2, title="Component 2 draft")

    # Now fetch the snapshots:
    _, as_of_checkpoint_1 = containers_api.get_entities_in_container_as_of(container, checkpoint_1.pk)
    assert [ev.entity_version.title for ev in as_of_checkpoint_1] == [
        "Component 1 as of checkpoint 1",
    ]
    _, as_of_checkpoint_2 = containers_api.get_entities_in_container_as_of(container, checkpoint_2.pk)
    assert [ev.entity_version.title for ev in as_of_checkpoint_2] == [
        "Component 1 as of checkpoint 2",
    ]
    _, as_of_checkpoint_3 = containers_api.get_entities_in_container_as_of(container, checkpoint_3.pk)
    assert [ev.entity_version.title for ev in as_of_checkpoint_3] == [
        "Component 1 as of checkpoint 3",
        "Component 2 as of checkpoint 3",
    ]
    _, as_of_checkpoint_4 = containers_api.get_entities_in_container_as_of(container, checkpoint_4.pk)
    assert [ev.entity_version.title for ev in as_of_checkpoint_4] == [
        "Child 1 🌴",  # Pinned. This title is self.component_1_v1.title (original v1 title)
        "Component 1 as of checkpoint 3",  # we didn't modify these components so they're same as in snapshot 3
        "Component 2 as of checkpoint 3",  # we didn't modify these components so they're same as in snapshot 3
    ]


# get_containers_with_entity


def test_get_containers_with_entity_draft(
    lp: LearningPackage,
    grandparent: ContainerContainer,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
    parent_of_six: TestContainer,
    child_entity1: TestEntity,
    child_entity2: TestEntity,
    child_entity3: TestEntity,
    lp2: LearningPackage,
    other_lp_parent: TestContainer,
    other_lp_child: TestEntity,
    django_assert_num_queries,
):
    """Test that we can efficiently get a list of all the draft containers containing a given entity."""

    # Note this test uses a lot of pre-loaded fixtures. Refer to the diagram in the comments near the top of this file.
    # The idea is to have enough variety to ensure we're testing comprehensively:
    # - duplicate entities in the same container
    # - pinned and unpinned entities
    # - different learning packages

    # "child_entity1" is found in three different containers:
    with django_assert_num_queries(1):
        result = list(containers_api.get_containers_with_entity(child_entity1.publishable_entity.id))
    assert result == [  # Note: ordering is in order of container creation
        parent_of_two.container,
        parent_of_three.container,
        parent_of_six.container,  # This should only appear once, not several times.
    ]

    # "child_entity3" is found in two different containers:
    with django_assert_num_queries(1):
        result = list(containers_api.get_containers_with_entity(child_entity3.publishable_entity.id))
    assert result == [  # Note: ordering is in order of container creation
        parent_of_three.container,  # pinned in this container
        parent_of_six.container,  # pinned and unpinned in this container
    ]

    # Test retrieving only "unpinned", for cases like potential deletion of a component, where we wouldn't care
    # about pinned uses anyways (they would be unaffected by a delete).

    with django_assert_num_queries(1):
        result = list(
            containers_api.get_containers_with_entity(child_entity3.publishable_entity.id, ignore_pinned=True)
        )
    assert result == [  # Note: ordering is in order of container creation
        parent_of_six.container,  # it's pinned and unpinned in this container
    ]

    # Some basic tests of the other learning package:
    assert list(containers_api.get_containers_with_entity(other_lp_child.publishable_entity.id)) == [
        other_lp_parent.container
    ]
    assert not list(containers_api.get_containers_with_entity(other_lp_parent.publishable_entity.id))


# get_container_children_count


def test_get_container_children_count(
    lp: LearningPackage,
    parent_of_two: TestContainer,
    parent_of_three: TestContainer,
    parent_of_six: TestContainer,
    grandparent: ContainerContainer,
):
    """Test `get_container_children_count()`"""
    publishing_api.publish_all_drafts(lp.id)
    assert containers_api.get_container_children_count(parent_of_two, published=False) == 2
    assert containers_api.get_container_children_count(parent_of_two, published=True) == 2

    assert containers_api.get_container_children_count(parent_of_three, published=False) == 3
    assert containers_api.get_container_children_count(parent_of_three, published=True) == 3

    assert containers_api.get_container_children_count(parent_of_six, published=False) == 6
    assert containers_api.get_container_children_count(parent_of_six, published=True) == 6
    # grandparent has two direct children - deeper descendants are not counted.
    assert containers_api.get_container_children_count(grandparent, published=False) == 2
    assert containers_api.get_container_children_count(grandparent, published=True) == 2

    # Add another container to "grandparent":
    containers_api.create_next_container_version(
        grandparent,
        entities=[parent_of_two, parent_of_three, parent_of_six],
        created=now,
        created_by=None,
    )
    # Warning: this is required if 'grandparent' is passed by ID to `create_next_container_version()`:
    # grandparent.refresh_from_db()
    assert containers_api.get_container_children_count(grandparent, published=False) == 3
    assert containers_api.get_container_children_count(grandparent, published=True) == 2  # published is unchanged


def test_get_container_children_count_soft_deletion(
    lp: LearningPackage,
    parent_of_two: TestContainer,
    parent_of_six: TestContainer,
    child_entity2: TestEntity,
):
    """Test `get_container_children_count()` when an entity is soft deleted"""
    publishing_api.publish_all_drafts(lp.id)
    publishing_api.soft_delete_draft(child_entity2.pk)
    # "parent_of_two" contains the soft deleted child, so its draft child count is decreased by one:
    assert containers_api.get_container_children_count(parent_of_two, published=False) == 1
    assert containers_api.get_container_children_count(parent_of_two, published=True) == 2
    # "parent_of_six" also contains two unpinned entries for the soft deleted child, so its draft child count is
    # decreased by two:
    assert containers_api.get_container_children_count(parent_of_six, published=False) == 4
    assert containers_api.get_container_children_count(parent_of_six, published=True) == 6


def test_get_container_children_count_queries(
    lp: LearningPackage,
    parent_of_two: TestContainer,
    parent_of_six: TestContainer,
    django_assert_num_queries,
):
    """Test how many database queries `get_container_children_count()` needs"""
    publishing_api.publish_all_drafts(lp.id)
    # The 6 queries are:
    # - Draft.objects.get()
    # - PublishableEntityVersion.objects.get()
    # - ContainerVersion.objects.get()
    # - TestContainerVersion.objects.get()
    # - EntityList.objects.get()
    # - SELECT COUNT(*) from EntityListRow ... JOIN on not soft deleted...
    # TODO: the first four/five queries are all just loading "TestContainer" and its related objects, and could be
    # optimized into a single query with better `select_related()`. The first four queries all use the same primary key.
    with django_assert_num_queries(6):
        assert containers_api.get_container_children_count(parent_of_two, published=False) == 2
    with django_assert_num_queries(6):
        assert containers_api.get_container_children_count(parent_of_two, published=True) == 2
    with django_assert_num_queries(6):
        assert containers_api.get_container_children_count(parent_of_six, published=False) == 6
    with django_assert_num_queries(6):
        assert containers_api.get_container_children_count(parent_of_six, published=True) == 6


# get_container_children_entity_refs


def test_get_container_children_entity_refs(grandparent: ContainerContainer, parent_of_six: TestContainer) -> None:
    """Test `get_container_children_entity_refs()`"""

    # TODO: is get_container_children_entity_refs() a useful API method? It's not used in edx-platform.

    assert containers_api.get_container_children_entity_refs(grandparent.versioning.draft) == [
        # These are the two children of "grandparent" - see diagram near the top of this file.
        "parent_of_two",
        "parent_of_three",
    ]

    assert containers_api.get_container_children_entity_refs(parent_of_six.versioning.draft) == [
        "child_entity3",
        "child_entity2",
        "child_entity1",
        "child_entity1",
        "child_entity2",
        "child_entity3",
    ]


# Container deletion


def test_soft_delete_container(lp: LearningPackage, parent_of_two: TestContainer, child_entity1: TestEntity):
    """
    I can delete a container without deleting the entities it contains.

    See https://github.com/openedx/frontend-app-authoring/issues/1693
    """
    # Publish everything:
    publish_entity(parent_of_two)
    # Delete the container:
    publishing_api.soft_delete_draft(parent_of_two.publishable_entity_id)
    parent_of_two.refresh_from_db()
    # Now the draft container is [soft] deleted, but the children, published container, and other container are
    # unaffected:
    assert parent_of_two.versioning.draft is None  # container is soft deleted.
    assert parent_of_two.versioning.published is not None
    child_entity1.refresh_from_db()
    assert child_entity1.versioning.draft is not None

    # Publish the changes:
    publishing_api.publish_all_drafts(lp.id)
    # Now the container's published version is also deleted, but nothing else is affected.
    parent_of_two.refresh_from_db()
    assert parent_of_two.versioning.draft is None
    assert parent_of_two.versioning.published is None  # Now this is also None
    child_entity1.refresh_from_db()
    assert child_entity1.versioning.draft == child_entity1.versioning.published
    assert child_entity1.versioning.draft is not None


def test_publish_container_without_children_should_fail(lp: LearningPackage):
    """
    Publishing a container with publish_dependencies=False when its unpinned
    children have never been published should either fail at publish time or
    produce a readable published state.

    If this validation is missing, the published container references child
    entities that have no Published row. Reading the published container's
    contents via get_entities_in_container(published=True) will crash with
    RelatedObjectDoesNotExist because row.entity.published doesn't exist for
    never-published children.
    """
    # Create child entities (draft-only, never published)
    child_1 = create_test_entity(lp, ref="unpublished_child_1", title="Unpublished Child 1")
    child_2 = create_test_entity(lp, ref="unpublished_child_2", title="Unpublished Child 2")

    # Create a container with unpinned references to these children
    container = create_test_container(
        lp,
        container_code="container_with_unpublished_children",
        title="Container with Unpublished Children",
        entities=[child_1, child_2],  # unpinned references
    )

    # Publish ONLY the container, skipping its dependencies (children).
    # This should either:
    # (a) raise an error at publish time (preventing the bad state), or
    # (b) produce a published state where get_entities_in_container works
    #     gracefully (e.g. returns an empty list for unpublished children).
    container_drafts = publishing_api.get_all_drafts(lp.id).filter(entity=container.publishable_entity)
    with pytest.raises(ValidationError, match="Cannot publish entities that have unpublished dependencies."):
        publishing_api.publish_from_drafts(
            lp.id,
            container_drafts,
            publish_dependencies=False,
        )

    # The children were never published, so reading the published container
    # should not crash. It should either raise a clear error, or gracefully
    # handle the missing children.
    container.refresh_from_db()
    assert container.versioning.published is None

    # If we changed to allow the above publish, then we'd want to check the following:
    # # This is the call that currently crashes with RelatedObjectDoesNotExist
    # # because the unpinned children have no Published row.
    # entries = containers_api.get_entities_in_container(
    #     container,
    #     published=True,
    # )
    # # If we get here without crashing, the children should be excluded since
    # # they were never published.
    # assert len(entries) == 0
