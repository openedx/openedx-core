"""
PublishableEntity model and PublishableEntityVersion + mixins
"""
from __future__ import annotations

from datetime import datetime
from functools import cached_property
from typing import ClassVar, Self

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext as _

from openedx_django_lib.fields import (
    case_insensitive_char_field,
    immutable_uuid_field,
    key_field,
    manual_date_time_field,
)
from openedx_django_lib.managers import WithRelationsManager

from .learning_package import LearningPackage


class PublishableEntity(models.Model):
    """
    This represents any publishable thing that has ever existed in a
    LearningPackage. It serves as a stable model that will not go away even if
    these things are later unpublished or deleted.

    A PublishableEntity belongs to exactly one LearningPackage.

    Examples of Publishable Entities
    --------------------------------

    Components (e.g. VideoBlock, ProblemBlock), Units, and Sections/Subsections
    would all be considered Publishable Entites. But anything that can be
    imported, exported, published, and reverted in a course or library could be
    modeled as a PublishableEntity, including things like Grading Policy or
    possibly Taxonomies (?).

    How to use this model
    ---------------------

    The publishing app understands that publishable entities exist, along with
    their drafts and published versions. It has some basic metadata, such as
    identifiers, who created it, and when it was created. It's meant to
    encapsulate the draft and publishing related aspects of your content, but
    the ``publishing`` app doesn't know anything about the actual content being
    referenced.

    You have to provide actual meaning to PublishableEntity by creating your own
    models that will represent your particular content and associating them to
    PublishableEntity via a OneToOneField with primary_key=True. The easiest way
    to do this is to have your model inherit from PublishableEntityMixin.

    Identifiers
    -----------
    The UUID is globally unique and should be treated as immutable.

    The key field *is* mutable, but changing it will affect all
    PublishedEntityVersions. They are locally unique within the LearningPackage.

    If you are referencing this model from within the same process, use a
    foreign key to the id. If you are referencing this PublishedEntity from an
    external system/service, use the UUID. The key is the part that is most
    likely to be human-readable, and may be exported/copied, but try not to rely
    on it, since this value may change.

    Note: When we actually implement the ability to change identifiers, we
    should make a history table and a modified attribute on this model.

    Why are Identifiers in this Model?
    ----------------------------------

    A PublishableEntity never stands alone–it's always intended to be used with
    a 1:1 model like Component or Unit. So why have all the identifiers in this
    model instead of storing them in those other models? Two reasons:

    * Published things need to have the right identifiers so they can be used
    throughout the system, and the UUID is serving the role of ISBN in physical
    book publishing.
    * We want to be able to enforce the idea that "key" is locally unique across
    all PublishableEntities within a given LearningPackage. Component and Unit
    can't do that without a shared model.

    That being said, models that build on PublishableEntity are free to add
    their own identifiers if it's useful to do so.

    Why not Inherit from this Model?
    --------------------------------

    Django supports multi-table inheritance:

      https://docs.djangoproject.com/en/4.2/topics/db/models/#multi-table-inheritance

    We don't use that, primarily because we want to more clearly decouple
    publishing concerns from the rest of the logic around Components, Units,
    etc. If you made a Component and ComponentVersion models that subclassed
    PublishableEntity and PublishableEntityVersion, and then accessed
    ``component.versions``, you might expect ComponentVersions to come back and
    be surprised when you get EntityVersions instead.

    In general, we want freedom to add new Publishing models, fields, and
    methods without having to worry about the downstream name collisions with
    other apps (many of which might live in other repositories). The helper
    mixins will provide a little syntactic sugar to make common access patterns
    more convenient, like file access.
    """

    uuid = immutable_uuid_field()
    learning_package = models.ForeignKey(
        LearningPackage,
        on_delete=models.CASCADE,
        related_name="publishable_entities",
    )

    # "key" is a reserved word for MySQL, so we're temporarily using the column
    # name of "_key" to avoid breaking downstream tooling. Consider renaming
    # this later.
    key = key_field(db_column="_key")

    created = manual_date_time_field()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    can_stand_alone = models.BooleanField(
        default=True,
        help_text=_("Set to True when created independently, False when created as part of a container."),
    )

    class Meta:
        constraints = [
            # Keys are unique within a given LearningPackage.
            models.UniqueConstraint(
                fields=[
                    "learning_package",
                    "key",
                ],
                name="oel_pub_ent_uniq_lp_key",
            )
        ]
        indexes = [
            # Global Key Index:
            #   * Search by key across all PublishableEntities on the site. This
            #     would be a support-oriented tool from Django Admin.
            models.Index(
                fields=["key"],
                name="oel_pub_ent_idx_key",
            ),
            # LearningPackage (reverse) Created Index:
            #  * Search for most recently *created* PublishableEntities for a
            #    given LearningPackage, since they're the most likely to be
            #    actively worked on.
            models.Index(
                fields=["learning_package", "-created"],
                name="oel_pub_ent_idx_lp_rcreated",
            ),
        ]
        # These are for the Django Admin UI.
        verbose_name = "Publishable Entity"
        verbose_name_plural = "Publishable Entities"

    def __str__(self):
        return f"{self.key}"


class PublishableEntityVersion(models.Model):
    """
    A particular version of a PublishableEntity.

    This model has its own ``uuid`` so that it can be referenced directly. The
    ``uuid`` should be treated as immutable.

    PublishableEntityVersions are created once and never updated. So for
    instance, the ``title`` should never be modified.

    Like PublishableEntity, the data in this model is only enough to cover the
    parts that are most important for the actual process of managing drafts and
    publishes. You will want to create your own models to represent the actual
    content data that's associated with this PublishableEntityVersion, and
    connect them using a OneToOneField with primary_key=True. The easiest way to
    do this is to inherit from PublishableEntityVersionMixin. Be sure to treat
    these versioned models in your app as immutable as well.

    .. no_pii
    """

    uuid = immutable_uuid_field()
    entity = models.ForeignKey(
        PublishableEntity, on_delete=models.CASCADE, related_name="versions"
    )

    # Most publishable things will have some sort of title, but blanks are
    # allowed for those that don't require one.
    title = case_insensitive_char_field(max_length=500, blank=True, default="")

    # The version_num starts at 1 and increments by 1 with each new version for
    # a given PublishableEntity. Doing it this way makes it more convenient for
    # users to refer to than a hash or UUID value. It also helps us catch race
    # conditions on save, by setting a unique constraint on the entity and
    # version_num.
    version_num = models.PositiveIntegerField(
        null=False,
        validators=[MinValueValidator(1)],
    )

    # All PublishableEntityVersions created as part of the same publish should
    # have the exact same created datetime (not off by a handful of
    # microseconds).
    created = manual_date_time_field()

    # User who created the PublishableEntityVersion. This can be null if the
    # user is later removed. Open edX in general doesn't let you remove users,
    # but we should try to model it so that this is possible eventually.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    dependencies: models.ManyToManyField[
        PublishableEntity, PublishableEntityVersionDependency
    ] = models.ManyToManyField(
        PublishableEntity,
        through="PublishableEntityVersionDependency",
        related_name="affects",
    )

    def __str__(self):
        return f"{self.entity.key} @ v{self.version_num} - {self.title}"

    class Meta:
        constraints = [
            # Prevent the situation where we have multiple
            # PublishableEntityVersions claiming to be the same version_num for
            # a given PublishableEntity. This can happen if there's a race
            # condition between concurrent editors in different browsers,
            # working on the same Publishable. With this constraint, one of
            # those processes will raise an IntegrityError.
            models.UniqueConstraint(
                fields=[
                    "entity",
                    "version_num",
                ],
                name="oel_pv_uniq_entity_version_num",
            )
        ]
        indexes = [
            # LearningPackage (reverse) Created Index:
            #   * Make it cheap to find the most recently created
            #     PublishableEntityVersions for a given LearningPackage. This
            #     represents the most recently saved work for a LearningPackage
            #     and would be the most likely areas to get worked on next.
            models.Index(
                fields=["entity", "-created"],
                name="oel_pv_idx_entity_rcreated",
            ),
            # Title Index:
            #   * Search by title.
            models.Index(
                fields=[
                    "title",
                ],
                name="oel_pv_idx_title",
            ),
        ]

        # These are for the Django Admin UI.
        verbose_name = "Publishable Entity Version"
        verbose_name_plural = "Publishable Entity Versions"


class PublishableEntityVersionDependency(models.Model):
    """
    Track the PublishableEntities that a PublishableEntityVersion depends on.

    For example, a partcular version of a Unit (U1.v1) might be defined to have
    unpinned references to Components C1 and C2. That means that any changes in
    C1 or C2 will affect U1.v1 via DraftSideEffects and PublishedSideEffects. We
    say that C1 and C2 are dependencies of U1.v1.

    An important restriction is that a PublishableEntityVersion's list of
    dependencies are defined when the version is created. It is not modified
    after that. No matter what happens to C1 or C2 (e.g. edit, deletion,
    un-deletion, reset-draft-version-to-published), they will always be
    dependencies of U1.v1.

    If someone removes C2 from U1, then that requires creating a new version of
    U1 (so U1.v2).

    This restriction is important because our ability to calculate and cache the
    state of "this version of this publishable entity and all its dependencies
    (children)" relies on this being true.

    .. no_pii
    """
    referring_version = models.ForeignKey(PublishableEntityVersion, on_delete=models.CASCADE)
    referenced_entity = models.ForeignKey(PublishableEntity, on_delete=models.RESTRICT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["referring_version", "referenced_entity"],
                name="oel_pevd_uniq_rv_re",
            )
        ]


class PublishableEntityMixin(models.Model):
    """
    Convenience mixin to link your models against PublishableEntity.

    Please see docstring for PublishableEntity for more details.

    If you use this class, you *MUST* also use PublishableEntityVersionMixin and
    the publishing app's api.register_publishable_models (see its docstring for
    details).
    """
    # select these related entities by default for all queries
    objects: ClassVar[WithRelationsManager[Self]] = WithRelationsManager(
        "publishable_entity",
        "publishable_entity__published",
        "publishable_entity__draft",
    )

    publishable_entity = models.OneToOneField(
        PublishableEntity, on_delete=models.CASCADE, primary_key=True
    )

    @cached_property
    def versioning(self):
        return self.VersioningHelper(self)

    @property
    def uuid(self) -> str:
        return self.publishable_entity.uuid

    @property
    def can_stand_alone(self) -> bool:
        return self.publishable_entity.can_stand_alone

    @property
    def key(self) -> str:
        return self.publishable_entity.key

    @property
    def created(self) -> datetime:
        return self.publishable_entity.created

    @property
    def created_by(self):
        return self.publishable_entity.created_by

    def __str__(self) -> str:
        return str(self.publishable_entity)

    class Meta:
        abstract = True

    class VersioningHelper:
        """
        Helper class to link content models to their versions.

        The publishing app has PublishableEntity and PublishableEntityVersion.
        This is a helper class so that if you mix PublishableEntityMixin into
        a content model like Component, then you can do something like::

            component.versioning.draft  # current draft ComponentVersion
            component.versioning.published  # current published ComponentVersion

        It links the relationships between content models and their versioned
        counterparts *through* the connection between PublishableEntity and
        PublishableEntityVersion. So ``component.versioning.draft`` ends up
        querying: Component -> PublishableEntity -> Draft ->
        PublishableEntityVersion -> ComponentVersion. But the people writing
        Component don't need to understand how the publishing models work to do
        these common queries.

        Caching Warning
        ---------------
        Note that because we're just using the underlying model's relations,
        calling this a second time will returned the cached relation, and
        not cause a fetch of new data from the database. So for instance, if
        you do::

            # Create a new Component + ComponentVersion
            component, component_version = create_component_and_version(
                learning_package_id=learning_package.id,
                namespace="xblock.v1",
                type="problem",
                local_key="monty_hall",
                title="Monty Hall Problem",
                created=now,
                created_by=None,
            )

            # This will work, because it's never been published
            assert component.versioning.published is None

            # Publishing happens
            publish_all_drafts(learning_package.id, published_at=now)

            # This will FAIL because it's going to use the relation value
            # cached on component instead of going to the database again.
            # You need to re-fetch the component for this to work.
            assert component.versioning.published == component_version

            # You need to manually refetch it from the database to see the new
            # publish status:
            component = get_component(component.pk)

            # Now this will work:
            assert component.versioning.published == component_version

        TODO: This probably means we should use a custom Manager to select
        related fields.
        """

        def __init__(self, content_obj):
            self.content_obj = content_obj

            self.content_version_model_cls = PublishableContentModelRegistry.get_versioned_model_cls(
                type(content_obj)
            )
            # Get the field that points from the *versioned* content model
            # (e.g. ComponentVersion) to the PublishableEntityVersion.
            field_to_pev = self.content_version_model_cls._meta.get_field(
                "publishable_entity_version"
            )
            # Now that we know the field that leads to PublishableEntityVersion,
            # get the reverse related field name so that we can use that later.
            self.related_name = field_to_pev.related_query_name()

            if field_to_pev.model != self.content_version_model_cls:
                # In the case of multi-table inheritance and mixins, this can get tricky.
                # Example:
                #   content_version_model_cls is UnitVersion, which is a subclass of ContainerVersion
                # This versioning helper can be accessed via unit_version.versioning (should return UnitVersion) or
                # via container_version.versioning (should return ContainerVersion)
                intermediate_model = field_to_pev.model  # example: ContainerVersion
                # This is the field on the subclass (e.g. UnitVersion) that gets
                # the intermediate (e.g. ContainerVersion). Example: "UnitVersion.container_version" (1:1 foreign key)
                field_to_intermediate = self.content_version_model_cls._meta.get_ancestor_link(intermediate_model)
                if field_to_intermediate:
                    # Example: self.related_name = "containerversion.unitversion"
                    self.related_name = self.related_name + "." + field_to_intermediate.related_query_name()

        def _content_obj_version(self, pub_ent_version: PublishableEntityVersion | None):
            """
            PublishableEntityVersion -> Content object version

            Given a reference to a PublishableEntityVersion, return the version
            of the content object that we've been mixed into.
            """
            if pub_ent_version is None:
                return None
            obj = pub_ent_version
            for field_name in self.related_name.split("."):
                obj = getattr(obj, field_name)
            return obj

        @property
        def draft(self):
            """
            Return the content version object that is the current draft.

            So if you mix ``PublishableEntityMixin`` into ``Component``, then
            ``component.versioning.draft`` will return you the
            ``ComponentVersion`` that is the current draft (not the underlying
            ``PublishableEntityVersion``).

            If this is causing many queries, it might be the case that you need
            to add ``select_related('publishable_entity__draft__version')`` to
            the queryset.
            """
            # Check if there's an entry in Drafts, i.e. has there ever been a
            # draft version of this PublishableEntity?
            if hasattr(self.content_obj.publishable_entity, 'draft'):
                # This can still be None if a draft existed at one point, but
                # was then "deleted". When deleting, the Draft row stays, but
                # the version it points to becomes None.
                draft_pub_ent_version = self.content_obj.publishable_entity.draft.version
            else:
                draft_pub_ent_version = None

            # The Draft.version points to a PublishableEntityVersion, so convert
            # that over to the class we actually want (were mixed into), e.g.
            # a ComponentVersion.
            return self._content_obj_version(draft_pub_ent_version)

        @property
        def latest(self):
            """
            Return the most recently created version for this content object.

            This can be None if no versions have been created.

            This is often the same as the draft version, but can differ if the
            content object was soft deleted or the draft was reverted.
            """
            return self.versions.order_by('-publishable_entity_version__version_num').first()

        @property
        def published(self):
            """
            Return the content version object that is currently published.

            So if you mix ``PublishableEntityMixin`` into ``Component``, then
            ``component.versioning.published`` will return you the
            ``ComponentVersion`` that is currently published (not the underlying
            ``PublishableEntityVersion``).

            If this is causing many queries, it might be the case that you need
            to add ``select_related('publishable_entity__published__version')``
            to the queryset.
            """
            # Check if there's an entry in Published, i.e. has there ever been a
            # published version of this PublishableEntity?
            if hasattr(self.content_obj.publishable_entity, 'published'):
                # This can still be None if something was published and then
                # later "deleted". When deleting, the Published row stays, but
                # the version it points to becomes None.
                published_pub_ent_version = self.content_obj.publishable_entity.published.version
            else:
                published_pub_ent_version = None

            # The Published.version points to a PublishableEntityVersion, so
            # convert that over to the class we actually want (were mixed into),
            # e.g. a ComponentVersion.
            return self._content_obj_version(published_pub_ent_version)

        @property
        def has_unpublished_changes(self):
            """
            Do we have unpublished changes?

            The simplest way to implement this would be to check self.published
            vs. self.draft, but that would cause unnecessary queries. This
            implementation should require no extra queries provided that the
            model was instantiated using a queryset that used a select related
            that has at least ``publishable_entity__draft`` and
            ``publishable_entity__published``.
            """
            pub_entity = self.content_obj.publishable_entity
            if hasattr(pub_entity, 'draft'):
                draft_version_id = pub_entity.draft.version_id
            else:
                draft_version_id = None
            if hasattr(pub_entity, 'published'):
                published_version_id = pub_entity.published.version_id
            else:
                published_version_id = None

            return draft_version_id != published_version_id

        @property
        def last_publish_log(self):
            """
            Return the most recent PublishLog for this component.

            Return None if the component is not published.
            """
            pub_entity = self.content_obj.publishable_entity
            if hasattr(pub_entity, 'published'):
                return pub_entity.published.publish_log_record.publish_log
            return None

        @property
        def versions(self):
            """
            Return a QuerySet of content version models for this content model.

            Example: If you mix PublishableEntityMixin into a Component model,
            This would return you a QuerySet of ComponentVersion models.
            """
            pub_ent = self.content_obj.publishable_entity
            return self.content_version_model_cls.objects.filter(
                publishable_entity_version__entity_id=pub_ent.id
            )

        def version_num(self, version_num):
            """
            Return a specific numbered version model.
            """
            pub_ent = self.content_obj.publishable_entity
            return self.content_version_model_cls.objects.get(
                publishable_entity_version__entity_id=pub_ent.id,
                publishable_entity_version__version_num=version_num,
            )


class PublishableEntityVersionMixin(models.Model):
    """
    Convenience mixin to link your models against PublishableEntityVersion.

    Please see docstring for PublishableEntityVersion for more details.

    If you use this class, you *MUST* also use PublishableEntityMixin and the
    publishing app's api.register_publishable_models (see its docstring for
    details).
    """

    # select these related entities by default for all queries
    objects: ClassVar[WithRelationsManager[Self]] = WithRelationsManager(
        "publishable_entity_version",
    )

    publishable_entity_version = models.OneToOneField(
        PublishableEntityVersion, on_delete=models.CASCADE, primary_key=True
    )

    @property
    def uuid(self) -> str:
        return self.publishable_entity_version.uuid

    @property
    def title(self) -> str:
        return self.publishable_entity_version.title

    @property
    def created(self) -> datetime:
        return self.publishable_entity_version.created

    @property
    def created_by(self):
        return self.publishable_entity_version.created_by

    @property
    def version_num(self) -> int:
        return self.publishable_entity_version.version_num

    def __str__(self) -> str:
        return str(self.publishable_entity_version)

    class Meta:
        abstract = True


class PublishableContentModelRegistry:
    """
    This class tracks content models built on PublishableEntity(Version).
    """

    _unversioned_to_versioned: dict[type[PublishableEntityMixin], type[PublishableEntityVersionMixin]] = {}
    _versioned_to_unversioned: dict[type[PublishableEntityVersionMixin], type[PublishableEntityMixin]] = {}

    @classmethod
    def register(
        cls,
        content_model_cls: type[PublishableEntityMixin],
        content_version_model_cls: type[PublishableEntityVersionMixin],
    ):
        """
        Register what content model maps to what content version model.

        If you want to call this from another app, please use the
        ``register_publishable_models`` function in this app's ``api`` module
        instead.
        """
        if not issubclass(content_model_cls, PublishableEntityMixin):
            raise ImproperlyConfigured(
                f"{content_model_cls} must inherit from PublishableEntityMixin"
            )
        if not issubclass(content_version_model_cls, PublishableEntityVersionMixin):
            raise ImproperlyConfigured(
                f"{content_version_model_cls} must inherit from PublishableEntityMixin"
            )

        cls._unversioned_to_versioned[content_model_cls] = content_version_model_cls
        cls._versioned_to_unversioned[content_version_model_cls] = content_model_cls

    @classmethod
    def get_versioned_model_cls(cls, content_model_cls):
        return cls._unversioned_to_versioned[content_model_cls]

    @classmethod
    def get_unversioned_model_cls(cls, content_version_model_cls):
        return cls._versioned_to_unversioned[content_version_model_cls]
