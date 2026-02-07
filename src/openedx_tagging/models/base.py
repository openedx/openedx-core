"""
Tagging app base data models
"""
from __future__ import annotations

import logging
import re
from typing import List

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Concat, Lower
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from typing_extensions import Self  # Until we upgrade to python 3.11

from openedx_django_lib.fields import MultiCollationTextField, case_insensitive_char_field, case_sensitive_char_field

from ..data import TagDataQuerySet
from .utils import RESERVED_TAG_CHARS, ConcatNull

log = logging.getLogger(__name__)


# Maximum depth allowed for a hierarchical taxonomy's tree of tags.
TAXONOMY_MAX_DEPTH = 3

# Ancestry of a given tag; the Tag.value fields of a given tag and its parents, starting from the root.
# Will contain 0...TAXONOMY_MAX_DEPTH elements.
Lineage = List[str]


class Tag(models.Model):
    """
    Represents a single value in a list or tree of values which can be applied to a particular Open edX object.

    Open edX tags are "name:value" pairs which can be applied to objects like content libraries, units, or people.
    Tag.taxonomy.name provides the "name" and the Tag.value provides the "value".
    (And an ObjectTag links a Tag with an object.)
    """

    id = models.BigAutoField(primary_key=True)
    taxonomy = models.ForeignKey(
        "Taxonomy",
        null=True,
        default=None,
        on_delete=models.CASCADE,
        help_text=_("Namespace and rules for using a given set of tags."),
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        default=None,
        on_delete=models.CASCADE,
        related_name="children",
        help_text=_(
            "Tag that lives one level up from the current tag, forming a hierarchy."
        ),
    )
    value = case_insensitive_char_field(
        max_length=500,
        help_text=_(
            "Content of a given tag, occupying the 'value' part of the key:value pair."
        ),
    )
    external_id = case_insensitive_char_field(
        max_length=255,
        null=True,  # To allow multiple values with our UNIQUE constraint, we need to use NULL values here instead of ""
        blank=True,
        help_text=_(
            "Used to link an Open edX Tag with a tag in an externally-defined taxonomy."
        ),
    )

    class Meta:
        indexes = [
            models.Index(fields=["taxonomy", "value"]),
            models.Index(fields=["taxonomy", "external_id"]),
        ]
        unique_together = [
            ["taxonomy", "external_id"],
            ["taxonomy", "value"],
        ]

    def __repr__(self):
        """
        Developer-facing representation of a Tag.
        """
        return str(self)

    def __str__(self):
        """
        User-facing string representation of a Tag.
        """
        return f"<{self.__class__.__name__}> ({self.id}) {self.value}"

    def display_str(self):
        """
        String representation of a Tag used on user logs.
        """
        if self.external_id:
            return f"<{self.__class__.__name__}> ({self.external_id} / {self.value})"
        return f"<{self.__class__.__name__}> ({self.value})"

    def get_lineage(self) -> Lineage:
        """
        Queries and returns the lineage of the current tag as a list of Tag.value strings.

        The root Tag.value is first, followed by its child.value, and on down to self.value.
        """
        lineage: Lineage = [self.value]
        next_ancestor = self.get_next_ancestor()
        while next_ancestor:
            lineage.insert(0, next_ancestor.value)
            next_ancestor = next_ancestor.get_next_ancestor()
        return lineage

    def get_next_ancestor(self) -> Tag | None:
        """
        Fetch the parent of this Tag.

        While doing so, preload several ancestors at the same time, so we can
        use fewer database queries than the basic approach of iterating through
        parent.parent.parent...
        """
        if self.parent_id is None:
            return None
        if not Tag.parent.is_cached(self):  # pylint: disable=no-member
            # Parent is not yet loaded. Retrieve our parent, grandparent, and great-grandparent in one query.
            # This is not actually changing the parent, just loading it and caching it.
            self.parent = Tag.objects.select_related("parent", "parent__parent").get(pk=self.parent_id)
        return self.parent

    @cached_property
    def depth(self) -> int:
        """
        How many ancestors this Tag has. Zero for root tags.
        """
        depth = 0
        tag = self
        while tag.parent:
            depth += 1
            tag = tag.parent
        return depth

    @staticmethod
    def annotate_depth(qs: models.QuerySet) -> models.QuerySet:
        """
        Given a query that loads Tag objects, annotate it with the depth of
        each tag.
        """
        return qs.annotate(depth=models.Case(
            models.When(parent_id=None, then=0),
            models.When(parent__parent_id=None, then=1),
            models.When(parent__parent__parent_id=None, then=2),
            models.When(parent__parent__parent__parent_id=None, then=3),
            # If the depth is 4 or more, currently we just "collapse" the depth
            # to 4 in order not to add too many joins to this query in general.
            default=4,
        ))

    @cached_property
    def child_count(self) -> int:
        """
        How many child tags this tag has in the taxonomy.
        """
        if self.taxonomy and not self.taxonomy.allow_free_text:
            return self.taxonomy.tag_set.filter(parent=self).count()
        return 0

    @cached_property
    def descendant_count(self) -> int:
        """
        How many descendant tags this tag has in the taxonomy.
        """
        if self.taxonomy and not self.taxonomy.allow_free_text:
            return self.taxonomy.tag_set.filter(
                Q(parent__parent=self) |
                Q(parent__parent__parent=self)
            ).count() + self.child_count
        return 0

    def clean(self):
        """
        Validate this tag before saving
        """
        # Don't allow leading or trailing whitespace:
        self.value = self.value.strip()
        if self.external_id:
            self.external_id = self.external_id.strip()

        for reserved_char in RESERVED_TAG_CHARS:
            if reserved_char in self.value:
                raise ValidationError(f"Tags cannot contain a '{reserved_char}' character.")

        if self.external_id and "\t" in self.external_id:
            raise ValidationError("Tag external ID cannot contain a TAB character.")


class Taxonomy(models.Model):
    """
    Represents a namespace and rules for a group of tags.
    """

    id = models.BigAutoField(primary_key=True)
    name = case_insensitive_char_field(
        null=False,
        max_length=255,
        db_index=True,
        help_text=_(
            "User-facing label used when applying tags from this taxonomy to Open edX objects."
        ),
    )
    description = MultiCollationTextField(
        blank=True,
        help_text=_(
            "Provides extra information for the user when applying tags from this taxonomy to an object."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=_("Only enabled taxonomies will be shown to authors."),
    )
    allow_multiple = models.BooleanField(
        default=True,
        help_text=_(
            "Indicates that multiple tags from this taxonomy may be added to an object."
        ),
    )
    allow_free_text = models.BooleanField(
        default=False,
        help_text=_(
            "Indicates that tags in this taxonomy need not be predefined; authors may enter their own tag values."
        ),
    )
    visible_to_authors = models.BooleanField(
        default=True,
        editable=False,
        help_text=_(
            "Indicates whether this taxonomy should be visible to object authors."
        ),
    )
    # External ID that should only be used on import/export.
    # NOT use for any other purposes, you can use the numeric ID of the model instead;
    # this id is editable.
    export_id = models.CharField(
        null=False,
        blank=False,
        max_length=255,
        help_text=_(
            "User-facing ID that is used on import/export."
            " Should only contain alphanumeric characters or '_' '-' '.'"
        ),
        unique=True,
    )
    _taxonomy_class = models.CharField(
        null=True,
        max_length=255,
        help_text=_(
            "Taxonomy subclass used to instantiate this instance; must be a fully-qualified module and class name."
            " If the module/class cannot be imported, an error is logged and the base Taxonomy class is used instead."
        ),
        blank=True,
    )

    class Meta:
        verbose_name_plural = "Taxonomies"

    def __repr__(self):
        """
        Developer-facing representation of a Taxonomy.
        """
        return str(self)

    def __str__(self):
        """
        User-facing string representation of a Taxonomy.
        """
        try:
            if self._taxonomy_class:
                return f"<{self.taxonomy_class.__name__}> ({self.id}) {self.name}"
        except ImportError:
            # Log error and continue
            log.exception(
                f"Unable to import taxonomy_class for {self.id}: {self._taxonomy_class}"
            )
        return f"<{self.__class__.__name__}> ({self.id}) {self.name}"

    @property
    def taxonomy_class(self) -> type[Taxonomy] | None:
        """
        Returns the Taxonomy subclass associated with this instance, or None if none supplied.

        May raise ImportError if a custom taxonomy_class cannot be imported.
        """
        if self._taxonomy_class:
            return import_string(self._taxonomy_class)
        return None

    @taxonomy_class.setter
    def taxonomy_class(self, taxonomy_class: type[Taxonomy] | None):
        """
        Assigns the given taxonomy_class's module path.class to the field.

        Must be a subclass of Taxonomy, or raises a ValueError.
        """
        if taxonomy_class:
            if not issubclass(taxonomy_class, Taxonomy):
                raise ValueError(
                    f"Unable to assign taxonomy_class for {self}: {taxonomy_class} must be a subclass of Taxonomy"
                )

            # ref: https://stackoverflow.com/a/2020083
            self._taxonomy_class = ".".join(
                [taxonomy_class.__module__, taxonomy_class.__qualname__]
            )
        else:
            self._taxonomy_class = None

    @property
    def system_defined(self) -> bool:
        """
        Indicates that tags and metadata for this taxonomy are maintained by the system;
        taxonomy admins will not be permitted to modify them.
        """
        return False

    def clean(self):
        super().clean()

        if not re.match(r'^[\w\-.]+$', self.export_id):
            raise ValidationError(
                "The export_id should only contain alphanumeric characters or '_' '-' '.'"
            )

    def cast(self):
        """
        Returns the current Taxonomy instance cast into its taxonomy_class.

        If no taxonomy_class is set, or if we're unable to import it, then just returns self.
        """
        try:
            TaxonomyClass = self.taxonomy_class
            if TaxonomyClass and not isinstance(self, TaxonomyClass):
                return TaxonomyClass().copy(self)
        except ImportError:
            # Log error and continue
            log.exception(
                f"Unable to import taxonomy_class for {self}: {self._taxonomy_class}"
            )

        return self

    def check_casted(self):
        """
        Double-check that this taxonomy has been cast() to a subclass if needed.
        """
        if self.cast() is not self:
            raise TypeError("Taxonomy was used incorrectly - without .cast()")

    def copy(self, taxonomy: Taxonomy) -> Taxonomy:
        """
        Copy the fields from the given Taxonomy into the current instance.
        """
        self.id = taxonomy.id
        self.name = taxonomy.name
        self.description = taxonomy.description
        self.enabled = taxonomy.enabled
        self.allow_multiple = taxonomy.allow_multiple
        self.allow_free_text = taxonomy.allow_free_text
        self.visible_to_authors = taxonomy.visible_to_authors
        self.export_id = taxonomy.export_id
        self._taxonomy_class = taxonomy._taxonomy_class  # pylint: disable=protected-access

        # Copy Django's internal prefetch_related cache to reduce queries required on the casted taxonomy.
        if hasattr(taxonomy, '_prefetched_objects_cache'):
            # pylint: disable=protected-access,attribute-defined-outside-init
            self._prefetched_objects_cache: dict = taxonomy._prefetched_objects_cache

        return self

    def get_filtered_tags(  # pylint: disable=too-many-positional-arguments
        self,
        depth: int | None = TAXONOMY_MAX_DEPTH,
        parent_tag_value: str | None = None,
        search_term: str | None = None,
        include_counts: bool = False,
        excluded_values: list[str] | None = None,
    ) -> TagDataQuerySet:
        """
        Returns a filtered QuerySet of tag values.
        For free text or dynamic taxonomies, this will only return tag values
        that have actually been used.

        By default returns all the tags of the given taxonomy

        Use `depth=1` to return a single level of tags, without any child
        tags included. Use `depth=None` or `depth=TAXONOMY_MAX_DEPTH` to return
        all descendants of the tags, up to our maximum supported depth.

        Use `parent_tag_value` to return only the children/descendants of a specific tag.

        Use `search_term` to filter the results by values that contains `search_term`.

        Use `excluded_values` to exclude tags with that value (and their parents, if applicable) from the results.

        Note: This is mostly an 'internal' API and generally code outside of openedx_tagging
        should use the APIs in openedx_tagging.api which in turn use this.
        """
        if self.allow_free_text:
            if parent_tag_value is not None:
                raise ValueError("Cannot specify a parent tag ID for free text taxonomies")
            result = self._get_filtered_tags_free_text(search_term=search_term, include_counts=include_counts)
            if excluded_values:
                return result.exclude(value__in=excluded_values)
            else:
                return result
        elif depth == 1:
            result = self._get_filtered_tags_one_level(
                parent_tag_value=parent_tag_value,
                search_term=search_term,
                include_counts=include_counts,
            )
            if excluded_values:
                return result.exclude(value__in=excluded_values)
            else:
                return result
        elif depth is None or depth == TAXONOMY_MAX_DEPTH:
            return self._get_filtered_tags_deep(
                parent_tag_value=parent_tag_value,
                search_term=search_term,
                include_counts=include_counts,
                excluded_values=excluded_values,
            )
        else:
            raise ValueError("Unsupported depth value for get_filtered_tags()")

    def _get_filtered_tags_free_text(
        self,
        search_term: str | None,
        include_counts: bool,
    ) -> TagDataQuerySet:
        """
        Implementation of get_filtered_tags() for free text taxonomies.
        """
        assert self.allow_free_text
        qs: models.QuerySet = self.objecttag_set.all()
        if search_term:
            qs = qs.filter(_value__icontains=search_term)
        # Rename "_value" to "value"
        qs = qs.annotate(value=F('_value'))
        # Add in all these fixed fields that don't really apply to free text tags, but we include for consistency:
        qs = qs.annotate(
            depth=Value(0),
            child_count=Value(0),
            descendant_count=Value(0),
            external_id=Value(None, output_field=models.CharField()),
            parent_value=Value(None, output_field=models.CharField()),
            _id=Value(None, output_field=models.CharField()),
        )
        qs = qs.values("value", "child_count", "depth", "parent_value", "external_id", "_id").order_by("value")
        if include_counts:
            return qs.annotate(usage_count=models.Count("value"))
        else:
            return qs.distinct()  # type: ignore[return-value]

    def _get_filtered_tags_one_level(
        self,
        parent_tag_value: str | None,
        search_term: str | None,
        include_counts: bool,
    ) -> TagDataQuerySet:
        """
        Implementation of get_filtered_tags() for closed taxonomies, where
        depth=1. When depth=1, we're only looking at a single "level" of the
        taxononomy, like all root tags or all children of a specific tag.
        """
        # A closed, and possibly hierarchical taxonomy. We're just fetching a single "level" of tags.
        if parent_tag_value:
            parent_tag = self.tag_for_value(parent_tag_value)
            qs: models.QuerySet = self.tag_set.filter(parent_id=parent_tag.pk)
            qs = qs.annotate(depth=Value(parent_tag.depth + 1))
            # Use parent_tag.value not parent_tag_value because they may differ in case
            qs = qs.annotate(parent_value=Value(parent_tag.value))
        else:
            qs = self.tag_set.filter(parent=None).annotate(depth=Value(0))  # type: ignore[no-redef]
            qs = qs.annotate(parent_value=Value(None, output_field=models.CharField()))
        qs = qs.annotate(child_count=models.Count("children", distinct=True))  # type: ignore[no-redef]
        qs = qs.annotate(grandchild_count=models.Count("children__children", distinct=True))
        qs = qs.annotate(great_grandchild_count=models.Count("children__children__children"))
        qs = qs.annotate(
            descendant_count=F("child_count") + F("grandchild_count") + F("great_grandchild_count")
        )  # type: ignore[no-redef]
        # Filter by search term:
        if search_term:
            qs = qs.filter(value__icontains=search_term)
        qs = qs.annotate(_id=F("id"))  # ID has an underscore to encourage use of 'value' rather than this internal ID
        qs = qs.values("value", "child_count", "descendant_count", "depth", "parent_value", "external_id", "_id")
        qs = qs.order_by("value")
        if include_counts:
            # We need to include the count of how many times this tag is used to tag objects.
            # You'd think we could just use:
            #     qs = qs.annotate(usage_count=models.Count("objecttag__pk"))
            # but that adds another join which starts creating a cross product and the children and usage_count become
            # intertwined and multiplied with each other. So we use a subquery.
            obj_tags = ObjectTag.objects.filter(tag_id=models.OuterRef("pk")).order_by().annotate(
                # We need to use Func() to get Count() without GROUP BY - see https://stackoverflow.com/a/69031027
                count=models.Func(F('id'), function='Count')
            )
            qs = qs.annotate(usage_count=models.Subquery(obj_tags.values('count')))
        return qs  # type: ignore[return-value]

    def _get_filtered_tags_deep(
        self,
        parent_tag_value: str | None,
        search_term: str | None,
        include_counts: bool,
        excluded_values: list[str] | None,
    ) -> TagDataQuerySet:
        """
        Implementation of get_filtered_tags() for closed taxonomies, where
        we're including tags from multiple levels of the hierarchy.
        """
        # All tags (possibly below a certain tag?) in the closed taxonomy, up to depth TAXONOMY_MAX_DEPTH
        if parent_tag_value:
            main_parent_id = self.tag_for_value(parent_tag_value).pk
        else:
            main_parent_id = None

        assert TAXONOMY_MAX_DEPTH == 3  # If we change TAXONOMY_MAX_DEPTH we need to change this query code:
        qs: models.QuerySet = self.tag_set.filter(
            Q(parent_id=main_parent_id) |
            Q(parent__parent_id=main_parent_id) |
            Q(parent__parent__parent_id=main_parent_id)
        )

        if search_term:
            # We need to do an additional query to find all the tags that match the search term, then limit the
            # search to those tags and their ancestors.
            matching_tags = qs.filter(value__icontains=search_term).values(
                'id', 'parent_id', 'parent__parent_id', 'parent__parent__parent_id'
            )
            if excluded_values:
                matching_tags = matching_tags.exclude(value__in=excluded_values)
            matching_ids = []
            for row in matching_tags:
                for pk in row.values():
                    if pk is not None:
                        matching_ids.append(pk)
            qs = qs.filter(pk__in=matching_ids)
            qs = qs.annotate(
                child_count=models.Count("children", filter=Q(children__pk__in=matching_ids), distinct=True),
                grandchild_count=models.Count(
                    "children__children", filter=Q(children__children__pk__in=matching_ids), distinct=True,
                ),
                great_grandchild_count=models.Count(
                    "children__children__children",
                    filter=Q(children__children__children__pk__in=matching_ids),
                ),
            )
            qs = qs.annotate(descendant_count=F("child_count") + F("grandchild_count") + F("great_grandchild_count"))
        elif excluded_values:
            raise NotImplementedError("Using excluded_values without search_term is not currently supported.")
            # We could implement this in the future but I'd prefer to get rid of the "excluded_values" API altogether.
            # It remains to be seen if it's useful to do that on the backend, or if we can do it better/simpler on the
            # frontend.
        else:
            qs = qs.annotate(child_count=models.Count("children", distinct=True))
            qs = qs.annotate(grandchild_count=models.Count("children__children", distinct=True))
            qs = qs.annotate(great_grandchild_count=models.Count("children__children__children"))
            qs = qs.annotate(descendant_count=F("child_count") + F("grandchild_count") + F("great_grandchild_count"))

        # Add the "depth" to each tag:
        qs = Tag.annotate_depth(qs)
        # Add the "lineage" as a field called "sort_key" to sort them in order correctly:
        qs = qs.annotate(sort_key=Lower(Concat(
            # For a root tag, we want sort_key="RootValue" and for a depth=1 tag
            # we want sort_key="RootValue\tValue". The following does that, since
            # ConcatNull(...) returns NULL if any argument is NULL.
            ConcatNull(F("parent__parent__parent__value"), Value("\t")),
            ConcatNull(F("parent__parent__value"), Value("\t")),
            ConcatNull(F("parent__value"), Value("\t")),
            F("value"),
            Value("\t"),  # We also need the '\t' separator character at the end, or MySQL will sort things wrong
            output_field=models.CharField(),
        )))
        # Add the parent value
        qs = qs.annotate(parent_value=F("parent__value"))
        qs = qs.annotate(_id=F("id"))  # ID has an underscore to encourage use of 'value' rather than this internal ID
        qs = qs.values("value", "child_count", "descendant_count", "depth", "parent_value", "external_id", "_id")
        qs = qs.order_by("sort_key")
        if include_counts:
            # Including the counts is a bit tricky; see the comment above in _get_filtered_tags_one_level()
            obj_tags = ObjectTag.objects.filter(tag_id=models.OuterRef("pk")).order_by().annotate(
                # We need to use Func() to get Count() without GROUP BY - see https://stackoverflow.com/a/69031027
                count=models.Func(F('id'), function='Count')
            )
            qs = qs.annotate(usage_count=models.Subquery(obj_tags.values('count')))
        return qs  # type: ignore[return-value]

    def add_tag(
        self,
        tag_value: str,
        parent_tag_value: str | None = None,
        external_id: str | None = None
    ) -> Tag:
        """
        Add new Tag to Taxonomy. If an existing Tag with the `tag_value` already
        exists in the Taxonomy, an exception is raised, otherwise the newly
        created Tag is returned
        """
        self.check_casted()

        if self.allow_free_text:
            raise ValueError(
                "add_tag() doesn't work for free text taxonomies. They don't use Tag instances."
            )

        if self.system_defined:
            raise ValueError(
                "add_tag() doesn't work for system defined taxonomies. They cannot be modified."
            )

        if self.tag_set.filter(value__iexact=tag_value).exists():
            raise ValueError(f"Tag with value '{tag_value}' already exists for taxonomy.")

        parent = None
        if parent_tag_value:
            # Get parent tag from taxonomy, raises Tag.DoesNotExist if doesn't
            # belong to taxonomy
            parent = self.tag_set.get(value__iexact=parent_tag_value)

        tag = Tag.objects.create(
            taxonomy=self, value=tag_value, parent=parent, external_id=external_id
        )
        tag.full_clean()

        return tag

    def update_tag(self, tag: str, new_value: str) -> Tag:
        """
        Update an existing Tag in Taxonomy and return it. Currently only
        supports updating the Tag's value.
        """
        self.check_casted()

        if self.allow_free_text:
            raise ValueError(
                "update_tag() doesn't work for free text taxonomies. They don't use Tag instances."
            )

        if self.system_defined:
            raise ValueError(
                "update_tag() doesn't work for system defined taxonomies. They cannot be modified."
            )

        # Update Tag instance with new value, raises Tag.DoesNotExist if
        # tag doesn't belong to taxonomy
        tag_to_update = self.tag_set.get(value__iexact=tag)
        tag_to_update.value = new_value
        tag_to_update.save()
        return tag_to_update

    def delete_tags(self, tags: List[str], with_subtags: bool = False):
        """
        Delete the Taxonomy Tags provided. If any of them have children and
        the `with_subtags` is not set to `True` it will fail, otherwise
        the sub-tags will be deleted as well.
        """
        self.check_casted()

        if self.allow_free_text:
            raise ValueError(
                "delete_tags() doesn't work for free text taxonomies. They don't use Tag instances."
            )

        if self.system_defined:
            raise ValueError(
                "delete_tags() doesn't work for system defined taxonomies. They cannot be modified."
            )

        tags_to_delete = self.tag_set.filter(value__in=tags)

        if tags_to_delete.count() != len(tags):
            # If they do not match that means there is one or more Tag ID(s)
            # provided that do not belong to this Taxonomy
            raise ValueError("Invalid tag id provided or tag id does not belong to taxonomy")

        # Check if any Tag contains subtags (children)
        contains_children = tags_to_delete.filter(children__isnull=False).distinct().exists()

        if contains_children and not with_subtags:
            raise ValueError(
                "Tag(s) contain children, `with_subtags` must be `True` for "
                "all Tags and their subtags (children) to be deleted."
            )

        # Delete the Tags with their subtags if any
        tags_to_delete.delete()

    def validate_value(self, value: str) -> bool:
        """
        Check if 'value' is part of this Taxonomy.
        A 'Tag' object may not exist for the value (e.g. if this is a free text
        taxonomy, then any value is allowed but no Tags are created; if this is
        a user taxonomy, Tag entries may only get created as needed.), but if
        this returns True then the value conceptually exists in this taxonomy
        and can be used to tag objects.
        """
        self.check_casted()
        if self.allow_free_text:
            return value != "" and isinstance(value, str)
        return self.tag_set.filter(value__iexact=value).exists()

    def tag_for_value(self, value: str) -> Tag:
        """
        Get the Tag object for the given value.
        Some Taxonomies may auto-create the Tag at this point, e.g. a User
        Taxonomy will create User Tags "just in time".

        Will raise Tag.DoesNotExist if the value is not valid for this taxonomy.
        """
        self.check_casted()
        if self.allow_free_text:
            raise ValueError("tag_for_value() doesn't work for free text taxonomies. They don't use Tag instances.")
        return self.tag_set.get(value__iexact=value)

    def validate_external_id(self, external_id: str) -> bool:
        """
        Check if 'external_id' is part of this Taxonomy.
        """
        self.check_casted()
        if self.allow_free_text:
            return False  # Free text taxonomies don't use 'external_id' on their tags
        return self.tag_set.filter(external_id__iexact=external_id).exists()

    def tag_for_external_id(self, external_id: str) -> Tag:
        """
        Get the Tag object for the given external_id.
        Some Taxonomies may auto-create the Tag at this point, e.g. a User
        Taxonomy will create User Tags "just in time".

        Will raise Tag.DoesNotExist if the tag is not valid for this taxonomy.
        """
        self.check_casted()
        if self.allow_free_text:
            raise ValueError("tag_for_external_id() doesn't work for free text taxonomies.")
        return self.tag_set.get(external_id__iexact=external_id)


class ObjectTag(models.Model):
    """
    Represents the association between a tag and an Open edX object.

    Tagging content in Open edX involves linking the object to a particular name:value "tag", where the "name" is the
    tag's label, and the value is the content of the tag itself.

    Tagging objects can be time-consuming for users, and so we guard against the deletion of Taxonomies and Tags by
    providing fields to cache the name:value stored for an object.

    However, sometimes Taxonomy names or Tag values change (e.g if there's a typo, or a policy change about how a label
    is used), and so we still store a link to the original Taxonomy and Tag, so that these changes will take precedence
    over the original name:value used.

    Also, if an ObjectTag is associated with free-text Taxonomy, then the tag's value won't be stored as a standalone
    Tag in the database -- it'll be stored here.
    """

    id = models.BigAutoField(primary_key=True)
    object_id = case_sensitive_char_field(
        max_length=255,
        db_index=True,
        editable=False,
        help_text=_("Identifier for the object being tagged"),
    )
    taxonomy = models.ForeignKey(
        Taxonomy,
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
        help_text=_(
            "Taxonomy that this object tag belongs to. "
            "Used for validating the tag and provides the tag's 'name' if set."
        ),
    )
    tag = models.ForeignKey(
        Tag,
        null=True,  # NULL in the case of free text taxonomies or when the Tag gets deleted.
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
        help_text=_(
            "Tag associated with this object tag. Provides the tag's 'value' if set."
        ),
    )
    is_copied = models.BooleanField(
        default=False,
        help_text=_(
            "True if this object tag has been copied from one object to another"
            " using 'copy_tags' api function"
        ),
    )
    _export_id = case_insensitive_char_field(
        max_length=255,
        help_text=_(
            "User-facing label used for this tag, stored in case taxonomy is (or becomes) null."
            " If the taxonomy field is set, then taxonomy.export_id takes precedence over this field."
        ),
    )
    _value = case_insensitive_char_field(
        max_length=500,
        help_text=_(
            "User-facing value used for this tag, stored in case tag is null, e.g if taxonomy is free text, or if it"
            " becomes null (e.g. if the Tag is deleted)."
            " If the tag field is set, then tag.value takes precedence over this field."
        ),
    )

    class Meta:
        indexes = [
            models.Index(fields=["taxonomy", "object_id"]),
            models.Index(fields=["taxonomy", "_value"]),
        ]
        unique_together = [
            ("object_id", "taxonomy", "tag_id"),
            ("object_id", "taxonomy", "_value"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.pk:  # This is a new instance:
            # Set _export_id and _value automatically on creation, if they weren't set:
            if not self._export_id and self.taxonomy:
                self._export_id = self.taxonomy.export_id
            if not self._value and self.tag:
                self._value = self.tag.value

    def __repr__(self):
        """
        Developer-facing representation of an ObjectTag.
        """
        return str(self)

    def __str__(self):
        """
        User-facing string representation of an ObjectTag.
        """
        if self.taxonomy:
            name = self.taxonomy.name
        else:
            name = self.export_id
        return f"<{self.__class__.__name__}> {self.object_id}: {name}={self.value}"

    @property
    def export_id(self) -> str:
        """
        Returns this tag's name/label.

        If taxonomy is set, then returns its name.
        Otherwise, returns the cached _export_id field.
        """
        return self.taxonomy.export_id if self.taxonomy else self._export_id

    @export_id.setter
    def export_id(self, export_id: str):
        """
        Stores to the _export_id field.
        """
        self._export_id = export_id

    @property
    def value(self) -> str:
        """
        Returns this tag's value.

        If tag is set, then returns its value.
        Otherwise, returns the cached _value field.
        """
        return self.tag.value if self.tag else self._value

    @value.setter
    def value(self, value: str):
        """
        Stores to the _value field.
        """
        self._value = value

    @property
    def is_deleted(self) -> bool:
        """
        Has this Tag been deleted from the Taxonomy? If so, we preserve this
        ObjecTag in the DB but it shouldn't be shown to the user.
        """
        return self.taxonomy is None or (self.tag is None and not self.taxonomy.allow_free_text)

    def clean(self):
        """
        Validate this ObjectTag.

        Note: this doesn't happen automatically on save(); only when edited in
        the django admin. So it's best practice to call obj_tag.full_clean()
        before saving.
        """
        if self.tag:
            if self.tag.taxonomy_id != self.taxonomy_id:
                raise ValidationError("ObjectTag's Taxonomy does not match Tag taxonomy")
            if self.tag.value != self._value:
                raise ValidationError("ObjectTag's _value is out of sync with Tag.value")
        else:
            # Note: self.taxonomy and/or self.tag may be NULL which is OK, because it means the Tag/Taxonomy
            # was deleted, but we still preserve this _value here in case the Taxonomy or Tag get re-created in future.
            if self._value == "":
                raise ValidationError("Invalid _value - empty string")
            for reserved_char in RESERVED_TAG_CHARS:
                if reserved_char in self.value:
                    raise ValidationError(f"Invalid _value - '{reserved_char}' is not allowed")
        if self.taxonomy and self.taxonomy.export_id != self._export_id:
            raise ValidationError("ObjectTag's _export_id is out of sync with Taxonomy.export_id")
        if "," in self.object_id or "*" in self.object_id:
            # Some APIs may use these characters to allow wildcard matches or multiple matches in the future.
            raise ValidationError("Object ID contains invalid characters")

    def get_lineage(self) -> Lineage:
        """
        Returns the lineage of the current tag as a list of value strings.

        If linked to a Tag, returns its lineage.
        Otherwise, returns an array containing its value string.
        """
        return self.tag.get_lineage() if self.tag else [self._value]

    def resync(self) -> bool:
        """
        Reconciles the stored ObjectTag properties with any changes made to its associated taxonomy or tag.

        This method is useful to propagate changes to a Taxonomy name or Tag value.

        It's also useful for a set of ObjectTags are imported from an external source prior to when a Taxonomy exists to
        validate or store its available Tags.

        Returns True if anything was changed, False otherwise.
        """
        changed = False

        # We used to have code here that would try to find a new taxonomy if the current taxonomy has been deleted.
        # But for now that's removed, as it risks things like linking a tag to the wrong org's taxonomy.

        # Sync the stored _export_id with the taxonomy.name
        if self.taxonomy and self._export_id != self.taxonomy.export_id:
            self.export_id = self.taxonomy.export_id
            changed = True

        # Sync taxonomy with matching _export_id
        if not self.taxonomy:
            taxonomy = Taxonomy.objects.filter(export_id=self.export_id).first()
            if taxonomy:
                self.taxonomy = taxonomy
                changed = True

        # Closed taxonomies require a tag matching _value
        if self.taxonomy and not self.taxonomy.allow_free_text and not self.tag_id:
            tag = self.taxonomy.tag_set.filter(value=self.value).first()
            if tag:
                self.tag = tag
                changed = True

        # Sync the stored _value with the tag.name
        elif self.tag and self._value != self.tag.value:
            self.value = self.tag.value
            changed = True

        return changed

    @classmethod
    def cast(cls, object_tag: ObjectTag) -> Self:
        """
        Returns a cls instance with the same properties as the given ObjectTag.
        """
        return cls().copy(object_tag)

    def copy(self, object_tag: ObjectTag) -> Self:
        """
        Copy the fields from the given ObjectTag into the current instance.
        """
        self.id = object_tag.id
        self.tag = object_tag.tag
        self.taxonomy = object_tag.taxonomy
        self.object_id = object_tag.object_id
        self.is_copied = object_tag.is_copied
        self._value = object_tag._value  # pylint: disable=protected-access
        self._export_id = object_tag._export_id  # pylint: disable=protected-access
        return self
