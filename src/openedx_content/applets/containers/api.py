"""
Containers API
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db.models import Model, QuerySet
from django.db.transaction import atomic
from django.db.utils import IntegrityError
from typing_extensions import TypeVar  # for 'default=...'

from ..publishing import api as publishing_api
from ..publishing.models import (
    LearningPackage,
    PublishableContentModelRegistry,
    PublishableEntity,
    PublishableEntityMixin,
    PublishableEntityVersion,
    PublishableEntityVersionMixin,
)
from .models import (
    Container,
    ContainerImplementationMissingError,
    ContainerType,
    ContainerVersion,
    EntityList,
    EntityListRow,
)

# A few of the APIs in this file are generic and can be used for Containers in
# general, or e.g. Units (subclass of Container) in particular. These type
# variables are used to provide correct typing for those generic API methods.
ContainerModel = TypeVar("ContainerModel", bound=Container)
ContainerVersionModel = TypeVar("ContainerVersionModel", bound=ContainerVersion, default=ContainerVersion)

# The public API that will be re-exported by openedx_content.api
# is listed in the __all__ entries below. Internal helper functions that are
# private to this module should start with an underscore. If a function does not
# start with an underscore AND it is not in __all__, that function is considered
# to be callable only by other applets in the openedx_content package.
__all__ = [
    "ContainerSubclass",
    "ContainerImplementationMissingError",
    "create_container",
    "create_container_version",
    "create_container_and_version",
    "create_next_container_version",
    "get_container",
    "get_container_version",
    "get_container_by_code",
    "get_all_container_subclasses",
    "get_container_subclass",
    "get_container_type_code_of",
    "get_container_subclass_of",
    "get_containers",
    "ChildrenEntitiesAction",
    "ContainerEntityListEntry",
    "get_entities_in_container",
    "get_entities_in_container_as_of",
    "contains_unpublished_changes",
    "get_containers_with_entity",
    "get_container_children_count",
    "get_container_children_entity_refs",
    "get_descendant_component_entity_ids",
]


@dataclass(frozen=True)
class ContainerEntityListEntry:
    """
    Data about a single entity in a container, e.g. a component in a unit.
    """

    entity_version: PublishableEntityVersion
    pinned: bool

    @property
    def entity(self):
        return self.entity_version.entity


EntityListInput = Iterable[
    PublishableEntity | PublishableEntityMixin | PublishableEntityVersion | PublishableEntityVersionMixin
]
ContainerSubclass = type[Container]


@dataclass(frozen=True, kw_only=True, slots=True)
class ParsedEntityReference:
    """
    Internal format to represent an entity, and/or a specific version of an
    entity. Used to construct entity lists.

    The public API contains `ContainerEntityListEntry` which plays a similar
    role, but is only used when reading data out, not mutating containers.
    """

    entity_pk: PublishableEntity.ID
    version_pk: int | None = None

    @staticmethod
    def parse(entities: EntityListInput) -> list[ParsedEntityReference]:
        """
        Helper method to create a list of entities in the correct format. If you
        pass `*Version` objects, they will be "frozen" at that version, whereas
        if you pass `*Entity` objects, they'll use the latest version.
        """
        new_list: list[ParsedEntityReference] = []
        for obj in entities:
            if isinstance(obj, PublishableEntityMixin):
                try:
                    obj = obj.publishable_entity
                except obj.__class__.publishable_entity.RelatedObjectDoesNotExist as exc:  # type: ignore[union-attr]
                    # If this happens, since it's a 1:1 relationship, likely both 'obj' (e.g. "Component") and
                    # 'obj.publishable_entity' have been deleted, so give a clearer error.
                    raise obj.DoesNotExist from exc
            elif isinstance(obj, PublishableEntityVersionMixin):
                obj = obj.publishable_entity_version

            if isinstance(obj, PublishableEntity):
                new_list.append(ParsedEntityReference(entity_pk=obj.id))
            elif isinstance(obj, PublishableEntityVersion):
                new_list.append(ParsedEntityReference(entity_pk=obj.entity_id, version_pk=obj.pk))
            else:
                raise TypeError(f"Unexpected entitity in list: {obj}")
        return new_list


def create_container(
    learning_package_id: LearningPackage.ID,
    container_code: str,
    created: datetime,
    created_by: int | None,
    *,
    container_cls: type[ContainerModel],
    can_stand_alone: bool = True,
) -> ContainerModel:
    """
    Create a new container.

    Args:
        learning_package_id: The ID of the learning package that contains the container.
        container_code: A local slug identifier for the container, unique within
            the learning package (regardless of container type).
        created: The date and time the container was created.
        created_by: The ID of the user who created the container
        container_cls: The subclass of container to create (e.g. `Unit`)
        can_stand_alone: Set to False when created as part of containers

    Returns:
        The newly created container as an instance of `container_cls`.
    """
    assert issubclass(container_cls, Container)
    assert container_cls is not Container, "Creating plain containers is not allowed; use a subclass of Container"
    entity_ref = container_code
    with atomic():
        # By convention, a Container's entity_ref is set to its container_code.
        # Do not bake this assumption into other systems. We may change it at some point.
        entity_ref = container_code
        publishable_entity = publishing_api.create_publishable_entity(
            learning_package_id,
            entity_ref,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
        )
        container = container_cls(
            publishable_entity=publishable_entity,
            container_code=container_code,
            learning_package_id=learning_package_id,
            container_type=container_cls.get_container_type(),
        )
        # Container subclasses use multi-table inheritance. Because the primary
        # key is already set (it's the PublishableEntity's), a plain save()
        # would first try an UPDATE on the parent Container table before
        # falling back to an INSERT. We know this is a new row, so force the
        # INSERT for every table in the inheritance chain.
        container.save(force_insert=(Model,))
    return container


def create_entity_list() -> EntityList:
    """
    Create a new entity list. This is an structure that holds a list of entities
    that will be referenced by the container.

    Returns:
        The newly created entity list.
    """
    return EntityList.objects.create()


def create_entity_list_with_rows(
    parsed_entities: list[ParsedEntityReference],
    *,
    learning_package_id: LearningPackage.ID | None,
) -> EntityList:
    """
    Create new entity list rows for an entity list.

    Args:
        entities: List of the entities that will comprise the entity list, in
            order. Pass `PublishableEntityVersion` or objects that use
            `PublishableEntityVersionMixin` to pin to a specific version. Pass
            `PublishableEntity` or objects that use `PublishableEntityMixin` for
            unpinned.
        learning_package_id: Optional. Verify that all the entities are from
            the specified learning package.

    Returns:
        The newly created entity list.
    """
    # Do a quick check that the given entities are in the right learning package:
    if learning_package_id:
        if (
            PublishableEntity.objects.filter(
                pk__in=[entity.entity_pk for entity in parsed_entities],
            )
            .exclude(
                learning_package_id=learning_package_id,
            )
            .exists()
        ):
            raise ValidationError("Container entities must be from the same learning package.")

    with atomic(savepoint=False):
        entity_list = create_entity_list()
        EntityListRow.objects.bulk_create(
            [
                EntityListRow(
                    entity_list=entity_list,
                    entity_id=entity.entity_pk,
                    order_num=order_num,
                    entity_version_id=entity.version_pk,
                )
                for order_num, entity in enumerate(parsed_entities)
            ]
        )
    return entity_list


def _create_container_version(
    container: Container,
    version_num: int,
    *,
    title: str,
    entity_list: EntityList,
    created: datetime,
    created_by: int | None,
) -> ContainerVersion:
    """
    Private internal method for logic shared by create_container_version() and
    create_next_container_version().
    """
    # validate entity_list using the type implementation:
    try:
        container_subclass = Container.subclass_for_type_code(container.container_type.type_code)
    except ContainerType.DoesNotExist as exc:
        raise IntegrityError(
            "Existing ContainerType is now missing. "
            "Likely your test case needs to call Container.reset_cache() because the cache contains "
            "a reference to a row that no longer exists after the test DB has been truncated. "
        ) from exc
    version_type = PublishableContentModelRegistry.get_versioned_model_cls(container_subclass)
    # Load the rows along with their entities and whatever relations this
    # container type's validate_entity() looks at, so that validating the
    # children is one query rather than a few queries per child.
    related_to_load = ["entity"] + [f"entity__{rel}" for rel in container_subclass.validate_entity_select_related]
    entity_rows = list(entity_list.entitylistrow_set.select_related(*related_to_load).order_by("order_num"))
    for entity_row in entity_rows:
        try:
            container_subclass.validate_entity(entity_row.entity)
        except ValidationError as exc:
            # This exception is carefully worded. The validation may have failed because the entity is of the wrong
            # type, but it _could_ be a of the correct type but otherwise invalid/corrupt, e.g. partially deleted.
            raise ValidationError(
                f'The entity "{entity_row.entity}" cannot be added to a "{container_subclass.type_code}" container.'
            ) from exc

    with atomic(savepoint=False):  # Make sure this will happen atomically but we don't need to create a new savepoint.
        publishable_entity_version = publishing_api.create_publishable_entity_version(
            container.publishable_entity_id,
            version_num=version_num,
            title=title,
            created=created,
            created_by=created_by,
            dependencies=[entity_row.entity_id for entity_row in entity_rows if entity_row.is_unpinned()],
        )
        container_version = version_type(
            publishable_entity_version=publishable_entity_version,
            container_id=container.id,
            entity_list=entity_list,
            # This could accept **kwargs in the future if we have additional type-specific fields?
        )
        # See the note in create_container() about multi-table inheritance.
        container_version.save(force_insert=(Model,))

    return container_version


def create_container_version(
    container_id: Container.ID,
    version_num: int,
    *,
    title: str,
    entities: EntityListInput,
    created: datetime,
    created_by: int | None,
) -> ContainerVersion:
    """
    Create a new container version.

    Args:
        container_id: The ID of the container that the version belongs to.
        version_num: The version number of the container.
        title: The title of the container.
        entities: List of the entities that will comprise the entity list, in
            order. Pass `PublishableEntityVersion` or objects that use
            `PublishableEntityVersionMixin` to pin to a specific version. Pass
            `PublishableEntity` or objects that use `PublishableEntityMixin` for
            unpinned.
        created: The date and time the container version was created.
        created_by: The ID of the user who created the container version.

    Returns:
        The newly created container version.
    """
    assert title is not None
    assert entities is not None

    container = Container.objects.select_related("publishable_entity", "container_type").get(pk=container_id)
    return _create_container_version_with_entities(
        container,
        version_num,
        title=title,
        entities=entities,
        created=created,
        created_by=created_by,
    )


def _create_container_version_with_entities(
    container: Container,
    version_num: int,
    *,
    title: str,
    entities: EntityListInput,
    created: datetime,
    created_by: int | None,
) -> ContainerVersion:
    """
    Create a new version of an already-loaded Container (see create_container_version).

    The ``container`` should have its ``publishable_entity`` and
    ``container_type`` relations loaded already.
    """
    with atomic(savepoint=False):
        entity = container.publishable_entity
        parsed_entities = ParsedEntityReference.parse(entities)
        entity_list = create_entity_list_with_rows(parsed_entities, learning_package_id=entity.learning_package_id)
        container_version = _create_container_version(
            container,
            version_num,
            title=title,
            entity_list=entity_list,
            created=created,
            created_by=created_by,
        )

    return container_version


def create_container_and_version(
    learning_package_id: LearningPackage.ID,
    container_code: str,
    *,
    title: str,
    container_cls: type[ContainerModel],
    entities: EntityListInput | None = None,
    created: datetime,
    created_by: int | None = None,
    can_stand_alone: bool = True,
) -> tuple[ContainerModel, ContainerVersionModel]:
    """

    Args:
        learning_package_id: The learning package ID.
        container_code: A local slug identifier for the container, unique within
            the learning package (regardless of container type).
        title: The title of the new container.
        container_cls: The subclass of container to create (e.g. Unit)
        entities: List of the entities that will comprise the entity list, in
            order. Pass `PublishableEntityVersion` or objects that use
            `PublishableEntityVersionMixin` to pin to a specific version. Pass
            `PublishableEntity` or objects that use `PublishableEntityMixin` for
            unpinned. Pass `None` for "no change".
        created: The creation date.
        created_by: The ID of the user who created the container.
        can_stand_alone: Set to False when created as part of containers
    """
    with atomic(savepoint=False):
        container = create_container(
            learning_package_id,
            container_code,
            created,
            created_by,
            can_stand_alone=can_stand_alone,
            container_cls=container_cls,
        )
        # We just created this container (with its publishable_entity and
        # container_type attached), so there's no need to re-fetch it.
        container_version: ContainerVersionModel = _create_container_version_with_entities(  # type: ignore[assignment]
            container,
            1,
            title=title,
            entities=entities or [],
            created=created,
            created_by=created_by,
        )
    return container, container_version


class ChildrenEntitiesAction(Enum):
    """Possible actions for children entities"""

    APPEND = "append"
    REMOVE = "remove"  # TODO: deprecated/drop/fix `REMOVE` - https://github.com/openedx/openedx-core/issues/502
    REPLACE = "replace"


def create_next_entity_list(
    learning_package_id: LearningPackage.ID,
    last_version: ContainerVersion,
    entities: EntityListInput,
    entities_action: ChildrenEntitiesAction = ChildrenEntitiesAction.REPLACE,
) -> EntityList:
    """
    Creates next entity list based on the given entities_action.

    Args:
        learning_package_id: Learning package ID
        last_version: Last version of container.
        entities: List of the entities that will comprise the entity list, in
            order. Pass `PublishableEntityVersion` or objects that use
            `PublishableEntityVersionMixin` to pin to a specific version. Pass
            `PublishableEntity` or objects that use `PublishableEntityMixin` for
            unpinned.
        entities_action: APPEND, REMOVE or REPLACE given entities from/to the container

    Returns:
        The newly created entity list.
    """
    parsed_entities = ParsedEntityReference.parse(entities)
    # Do a quick check that the given entities are in the right learning package:
    if (
        PublishableEntity.objects.filter(pk__in=[entity.entity_pk for entity in parsed_entities])
        .exclude(learning_package_id=learning_package_id)
        .exists()
    ):
        raise ValidationError("Container entities must be from the same learning package.")

    if entities_action == ChildrenEntitiesAction.APPEND:
        # get previous entity list rows
        last_entities = last_version.entity_list.entitylistrow_set.only("entity_id", "entity_version_id").order_by(
            "order_num"
        )
        # append given entity_rows to the existing children
        parsed_entities = [
            ParsedEntityReference(entity_pk=entity.entity_id, version_pk=entity.entity_version_id)
            for entity in last_entities
        ] + parsed_entities
    elif entities_action == ChildrenEntitiesAction.REMOVE:
        # get previous entity list:
        last_entities_qs = last_version.entity_list.entitylistrow_set.only("entity_id", "entity_version_id").order_by(
            "order_num"
        )
        # Filter out the entities to remove:
        for entity in parsed_entities:
            last_entities_qs = last_entities_qs.exclude(entity_id=entity.entity_pk, entity_version_id=entity.version_pk)
        # Create the new entity list:
        parsed_entities = [
            ParsedEntityReference(entity_pk=entity.entity_id, version_pk=entity.entity_version_id)
            for entity in last_entities_qs.all()
        ]

    return create_entity_list_with_rows(parsed_entities, learning_package_id=learning_package_id)


def create_next_container_version(
    container: Container | Container.ID,
    /,
    *,
    title: str | None = None,
    entities: EntityListInput | None = None,
    created: datetime,
    created_by: int | None,
    entities_action: ChildrenEntitiesAction = ChildrenEntitiesAction.REPLACE,
    force_version_num: int | None = None,
) -> ContainerVersion:
    """
    Create the next version of a container. A new version of the container is created
    only when its metadata changes:

    * Something was added to the Container.
    * We re-ordered the rows in the container.
    * Something was removed from the container.
    * The Container's metadata changed, e.g. the title.
    * We pin to different versions of the Container.

    Args:
        id: The ID of the container to create the next version of.
        title: The title of the container. None to keep the current title.
        entities: List of the entities that will comprise the entity list, in
            order. Pass `PublishableEntityVersion` or objects that use
            `PublishableEntityVersionMixin` to pin to a specific version. Pass
            `PublishableEntity` or objects that use `PublishableEntityMixin` for
            unpinned. Pass `None` for "no change".
        created: The date and time the container version was created.
        created_by: The ID of the user who created the container version.
        force_version_num (int, optional): If provided, overrides the automatic version number increment and sets
            this version's number explicitly. Use this if you need to restore or import a version with a specific
            version number, such as during data migration or when synchronizing with external systems.

    Returns:
        The newly created container version. Note: it will be a subclass of `ContainerVersion`

    Why use force_version_num?
        Normally, the version number is incremented automatically from the latest version.
        If you need to set a specific version number (for example, when restoring from backup,
        importing legacy data, or synchronizing with another system),
        use force_version_num to override the default behavior.
    """
    with atomic():
        if isinstance(container, int):
            container = Container.objects.select_related("publishable_entity", "container_type").get(pk=container)
        assert isinstance(container, Container)
        entity = container.publishable_entity
        last_version = container.versioning.latest
        if last_version is None:
            next_version_num = 1
        else:
            next_version_num = last_version.version_num + 1

        if force_version_num is not None:
            next_version_num = force_version_num

        if entities is None and last_version is not None:
            # We're only changing metadata. Keep the same entity list.
            next_entity_list = last_version.entity_list
        else:
            next_entity_list = create_next_entity_list(
                entity.learning_package_id, last_version, entities if entities is not None else [], entities_action
            )

        next_container_version = _create_container_version(
            container,
            next_version_num,
            title=title if title is not None else last_version.title,
            entity_list=next_entity_list,
            created=created,
            created_by=created_by,
        )

    # reset any potentially cached 'container.versioning.draft' value on the passed 'container' instance, since we've
    # definitely modified the draft. If 'container' is local to this function, this has no effect.
    if PublishableEntity.draft.is_cached(container.publishable_entity):  # pylint: disable=no-member
        PublishableEntity.draft.related.delete_cached_value(container.publishable_entity)  # pylint: disable=no-member
    return next_container_version


def get_container(pk: Container.ID) -> Container:
    """
    Get a container by its primary key.

    This returns the Container, not any specific version. It may not be published, or may have been soft deleted.

    Args:
        pk: The primary key of the container.

    Returns:
        The container with the given primary key.
    """
    return Container.objects.get(pk=pk)


def get_container_version(container_version_pk: int) -> ContainerVersion:
    """
    Get a container version by its primary key.

    Args:
        pk: The primary key of the container version.

    Returns:
        The container version with the given primary key.
    """
    return ContainerVersion.objects.get(pk=container_version_pk)


def get_container_by_code(learning_package_id: LearningPackage.ID, /, container_code: str) -> Container:
    """
    Get a container by its learning package and container code.

    Args:
        learning_package_id: The ID of the learning package that contains the container.
        container_code: The container code of the container.

    Returns:
        The container with the given container code (as `Container`, not as its typed subclass).
    """
    try:
        return Container.objects.select_related("container_type").get(
            learning_package_id=learning_package_id,
            container_code=container_code,
        )
    except Container.DoesNotExist:
        # Check if it's the container or the learning package that does not exist:
        try:
            LearningPackage.objects.get(pk=learning_package_id)
        except LearningPackage.DoesNotExist as lp_exc:
            raise lp_exc  # No need to "raise from" as LearningPackage nonexistence is more important
        raise


def get_all_container_subclasses() -> list[ContainerSubclass]:
    """
    Get a list of installed Container types (`Container` subclasses).
    """
    return Container.all_subclasses()


def get_container_subclass(type_code: str, /) -> ContainerSubclass:
    """
    Get subclass of `Container` from its `type_code` string (e.g. `"unit"`).

    Will raise a `ContainerImplementationMissingError` if the type is not currently installed.
    """
    return Container.subclass_for_type_code(type_code)


def get_container_type_code_of(container: Container | Container.ID, /) -> str:
    """Get the type of a container, as a string - e.g. "unit"."""
    if isinstance(container, int):
        container = get_container(container)
    assert isinstance(container, Container)
    return container.container_type.type_code


def get_container_subclass_of(container: Container | Container.ID, /) -> ContainerSubclass:
    """
    Get the type of a container.

    Works on either a generic `Container` instance or an instance of a specific
    subclass like `Unit`. Accepts an instance or an integer primary key.

    Will raise a `ContainerImplementationMissingError` if the type is not currently installed.
    """
    type_code = get_container_type_code_of(container)
    return Container.subclass_for_type_code(type_code)


def get_containers(
    learning_package_id: LearningPackage.ID,
    include_deleted: bool | None = False,
) -> QuerySet[Container]:
    """
    Get all containers in the given learning package.

    Args:
        learning_package_id: The primary key of the learning package
        include_deleted: If True, include deleted containers (with no draft version) in the result.

    Returns:
        A queryset containing the container associated with the given learning package.
    """
    # A query pattern that gets all containers is likely to need the container type info, so preload that while we're
    # at it.
    container_qset = Container.objects.select_related("container_type").filter(
        publishable_entity__learning_package=learning_package_id,
    )
    if not include_deleted:
        container_qset = container_qset.filter(publishable_entity__draft__version__isnull=False)

    return container_qset.order_by("pk")


def get_entities_in_container(
    container: Container,
    *,
    published: bool,
    select_related_version: str | None = None,
) -> list[ContainerEntityListEntry]:
    """
    Get the list of entities and their versions in the current draft or
    published version of the given container.

    Args:
        container: The Container, e.g. returned by `get_container()`
        published: `True` if we want the published version of the container, or
            `False` for the draft version.
        select_related_version: An optional optimization; specify a relationship
        on ContainerVersion, like `componentversion` or `containerversion__x`
        to preload via select_related.
    """
    assert isinstance(container, Container)
    if published:
        # Very minor optimization: reload the container with related 1:1 entities
        container = Container.objects.select_related(
            "publishable_entity__published__version__containerversion__entity_list"
        ).get(pk=container.id)
        container_version = container.versioning.published
        select_related = ["entity__published__version"]
        if select_related_version:
            select_related.append(f"entity__published__version__{select_related_version}")
    else:
        # Very minor optimization: reload the container with related 1:1 entities
        container = Container.objects.select_related(
            "publishable_entity__draft__version__containerversion__entity_list"
        ).get(pk=container.id)
        container_version = container.versioning.draft
        select_related = ["entity__draft__version"]
        if select_related_version:
            select_related.append(f"entity__draft__version__{select_related_version}")
    if container_version is None:
        raise ContainerVersion.DoesNotExist  # This container has not been published yet, or has been deleted.
    assert isinstance(container_version, ContainerVersion)
    entity_list: list[ContainerEntityListEntry] = []
    for row in container_version.entity_list.entitylistrow_set.select_related(
        "entity_version",
        *select_related,
    ).order_by("order_num"):
        entity_version = row.entity_version  # This will be set if pinned
        if not entity_version:  # If this entity is "unpinned", use the latest published/draft version:
            entity_version = row.entity.published.version if published else row.entity.draft.version
        if entity_version is not None:  # As long as this hasn't been soft-deleted:
            entity_list.append(
                ContainerEntityListEntry(
                    entity_version=entity_version,
                    pinned=row.entity_version is not None,
                )
            )
        # else we could indicate somehow a deleted item was here, e.g. by returning a ContainerEntityListEntry with
        # deleted=True, but we don't have a use case for that yet.
    return entity_list


def get_entities_in_container_as_of(
    container: Container,
    publish_log_id: int,
) -> tuple[ContainerVersion | None, list[ContainerEntityListEntry]]:
    """
    Get the list of entities and their versions in the published version of the
    given container as of the given PublishLog version (which is essentially a
    version for the entire learning package).

    Also returns the ContainerVersion so you can see the container title,
    settings?, and any other metadata from that point in time.

    TODO: optimize, perhaps by having the publishlog store a record of all
          ancestors of every modified PublishableEntity in the publish.
    """
    assert isinstance(container, Container)
    pub_entity_version = publishing_api.get_published_version_as_of(container.publishable_entity_id, publish_log_id)
    if pub_entity_version is None:
        return None, []  # This container was not published as of the given PublishLog ID.
    container_version = pub_entity_version.containerversion

    entity_list: list[ContainerEntityListEntry] = []
    rows = container_version.entity_list.entitylistrow_set.order_by("order_num")
    for row in rows:
        if row.entity_version is not None:
            # Pinned child entity:
            entity_list.append(ContainerEntityListEntry(entity_version=row.entity_version, pinned=True))
        else:
            # Unpinned entity - figure out what its latest published version was.
            # This is not optimized. It could be done in one query per unit rather than one query per component.
            pub_entity_version = publishing_api.get_published_version_as_of(row.entity_id, publish_log_id)
            if pub_entity_version:
                entity_list.append(ContainerEntityListEntry(entity_version=pub_entity_version, pinned=False))
    return container_version, entity_list


def contains_unpublished_changes(container_or_pk: Container | Container.ID, /) -> bool:
    """
    Check recursively if a container has any unpublished changes.

    Note: I've preserved the API signature for now, but we probably eventually
    want to make a more general function that operates on PublishableEntities
    and dependencies, once we introduce those with courses and their files,
    grading policies, etc.

    Note: unlike this method, the similar-sounding
    `container.versioning.has_unpublished_changes` property only reports
    if the container itself has unpublished changes, not
    if its contents do. So if you change a title or add a new child component,
    `has_unpublished_changes` will be `True`, but if you merely edit a component
    that's in the container, it will be `False`. This method will return `True`
    in either case.
    """
    if isinstance(container_or_pk, int):
        container_id = container_or_pk
    else:
        assert isinstance(container_or_pk, Container)
        container_id = container_or_pk.id
    container = (
        Container.objects.select_related("publishable_entity__draft__draft_log_record")
        .select_related("publishable_entity__published__publish_log_record")
        .get(pk=container_id)
    )
    if container.versioning.has_unpublished_changes:
        return True

    draft = container.publishable_entity.draft
    published = container.publishable_entity.published

    # Edge case: A container that was created and then immediately soft-deleted
    # does not contain any unpublished changes.
    if draft is None and published is None:
        return False

    # The dependencies_hash_digest captures the state of all descendants, so we
    # can do this quick comparison instead of iterating through layers of
    # containers.
    draft_version_hash_digest = draft.log_record.dependencies_hash_digest
    published_version_hash_digest = published.log_record.dependencies_hash_digest

    return draft_version_hash_digest != published_version_hash_digest


def get_containers_with_entity(
    publishable_entity_pk: PublishableEntity.ID,
    *,
    ignore_pinned=False,
    published=False,
) -> QuerySet[Container]:
    """
    Find all draft containers that directly contain the given entity.

    They will always be from the same learning package; cross-package containers
    are not allowed.

    Args:
        publishable_entity_pk: The ID of the PublishableEntity to search for.
        ignore_pinned: if true, ignore any pinned references to the entity.
    """
    branch = "published" if published else "draft"
    if ignore_pinned:
        filter_dict = {
            # Note: these two conditions must be in the same filter() call,
            # or the query won't be correct.
            (
                f"publishable_entity__{branch}__version__containerversion__entity_list__entitylistrow__entity_id"
            ): publishable_entity_pk,
            (
                f"publishable_entity__{branch}__version__"
                "containerversion__entity_list__entitylistrow__entity_version_id"
            ): None,
        }
        qs = Container.objects.filter(**filter_dict)
    else:
        filter_dict = {
            (
                f"publishable_entity__{branch}__version__containerversion__entity_list__entitylistrow__entity_id"
            ): publishable_entity_pk
        }
        qs = Container.objects.filter(**filter_dict)

    return qs.order_by("pk").distinct()  # Ordering is mostly for consistent test cases.


def get_container_children_count(
    container: Container,
    *,
    published: bool,
):
    """
    Get the count of entities in the current draft or published version of the given container.

    Args:
        container: The Container, e.g. returned by `get_container()`
        published: `True` if we want the published version of the container, or
            `False` for the draft version.
    """
    assert isinstance(container, Container)
    container_version = container.versioning.published if published else container.versioning.draft
    if container_version is None:
        raise ContainerVersion.DoesNotExist  # This container has not been published yet, or has been deleted.
    assert isinstance(container_version, ContainerVersion)
    if published:
        filter_deleted = {"entity__published__version__isnull": False}
    else:
        filter_deleted = {"entity__draft__version__isnull": False}
    return container_version.entity_list.entitylistrow_set.filter(**filter_deleted).count()


def get_container_children_entity_refs(container_version: ContainerVersion) -> list[str]:
    """
    Fetch the list of entity refs for all entities in the given container version.

    Args:
        container_version: The ContainerVersion to fetch the entity refs for.
    Returns:
        A list of entity refs for all entities in the container version, ordered by position.
    """
    return list(
        container_version.entity_list.entitylistrow_set
        .values_list("entity__entity_ref", flat=True)
        .order_by("order_num")
    )


def get_descendant_component_entity_ids(container: Container) -> list[int]:
    """
    Return entity IDs of all leaf (non-Container) descendants of ``container``.

    Intermediate containers (e.g. Subsections, Units) are never included in the
    result; only leaf component entities are returned.

    The traversal follows draft state only. Soft-deleted children are skipped
    automatically because ``get_entities_in_container`` omits them.

    Edge cases:

    - A container whose draft was soft-deleted has no children to traverse and
      contributes no entity IDs.
    - An entity that appears as a child of multiple containers is deduplicated
      because the result is built from a set.
    - A cycle-guard (``visited_container_pks``) prevents infinite loops, which
      cannot occur in practice but is included for safety.
    """
    all_component_ids: set[int] = set()
    containers_to_visit: list[Container] = [container]
    visited_container_pks: set[int] = {container.pk}

    while containers_to_visit:
        current = containers_to_visit.pop()
        try:
            children = get_entities_in_container(
                current,
                published=False,
                select_related_version="containerversion__container",
            )
        except ContainerVersion.DoesNotExist:
            continue

        for entry in children:
            try:
                child_container = entry.entity_version.containerversion.container
                if child_container.pk not in visited_container_pks:
                    visited_container_pks.add(child_container.pk)
                    containers_to_visit.append(child_container)
            except ContainerVersion.DoesNotExist:
                all_component_ids.add(entry.entity.pk)

    return list(all_component_ids)
