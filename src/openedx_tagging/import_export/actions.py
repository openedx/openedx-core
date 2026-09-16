"""
Actions for import tags
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from django.utils.translation import gettext as _

from ..models import Tag, Taxonomy
from .exceptions import ImportActionConflict, ImportActionError

if TYPE_CHECKING:
    from .import_plan import TagItem


class ImportAction:
    """
    Base class to create actions

    Each action is a simple operation to be performed on the database.
    There are no compound actions or actions that have to do with each other.

    To create an Action you need to implement the following:

    Given a TagItem, the actions to be performed must be deduced
    by comparing with the tag on the database.
    Ex. The create action is inferred if the tag does not exist in the database.
    This check is done in `applies_for`

    Then each action validates if the change is consistent with the database
    or with previous actions.
    Ex. Verify that when creating a tag, there is not a previous creation action
    that has the same tag_id.
    This checks is done in `validate`

    Then the actions are executed. Ex. Create the tag on the database
    This is done in `execute`
    """

    name = "import_action"

    def __init__(self, taxonomy: Taxonomy, tag: TagItem, index: int, target_pk: int | None = None):
        self.taxonomy = taxonomy
        self.tag = tag
        self.index = index
        self.target_pk = target_pk

    def __repr__(self) -> str:
        return str(_("Action {name} (index={index},id={id})").format(name=self.name, index=self.index, id=self.tag.id))

    def __str__(self) -> str:
        return self.__repr__()

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        Implement this to meet the conditions that a `TagItem` needs
        to have for this action. If this function returns `True` for `tag`
        then the action is created.
        """
        raise NotImplementedError

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        Implement this to find inconsistencies with tags in the
        database or with previous actions.
        """
        raise NotImplementedError

    def execute(self) -> None:
        """
        Implement this to execute the action.
        """
        raise NotImplementedError

    def _get_tag(self) -> Tag:
        """
        Returns the respective tag of this actions
        """
        if self.tag.id:
            try:
                return self.taxonomy.tag_set.get(external_id=self.tag.id)
            except Tag.DoesNotExist:
                pass
        return self.taxonomy.tag_set.get(value=self.tag.value, external_id=None)

    def _search_action(
        self,
        indexed_actions: dict,
        action_name: str,
        attr: str,
        search_value: str | None,
    ):
        """
        Use this function to find and action using an `attr` of `TagItem`
        """
        for action in indexed_actions[action_name]:
            if search_value == getattr(action.tag, attr):
                return action

        return None

    def _validate_parent(self, indexed_actions) -> ImportActionError | None:
        """
        Helper method to validate that the parent tag has already been defined.

        `parent_id` must match a tag's *end-state* external_id, not one
        that's being renamed away in this same import (see `_vacated_pks`):
        a stale match falls through to the same "created or renamed-in
        earlier in this import" check used for a brand-new parent.
        """
        try:
            # Validates that the parent exists on the taxonomy
            parent_tag = self.taxonomy.tag_set.get(external_id=self.tag.parent_id)
            if parent_tag.pk in indexed_actions.get("_vacated_pks", set()):
                raise Tag.DoesNotExist
        except Tag.DoesNotExist:
            # Or if the parent is created or renamed-in on previous actions
            found = self._search_action(
                indexed_actions, CreateTag.name, "id", self.tag.parent_id
            ) or self._search_action(
                indexed_actions, RenameTagExternalId.name, "id", self.tag.parent_id
            )
            if not found:
                return ImportActionError(
                    action=self,
                    message=_(
                        "Unknown parent tag ({parent_id}). "
                        "You need to add parent before the child in your file."
                    ).format(parent_id=self.tag.parent_id),
                )
        return None

    def _validate_value(self, indexed_actions) -> ImportActionError | None:
        """
        Check for value duplicates in the models and in previous create/rename
        actions
        """
        try:
            is_deleted_tag_value = any(
                self.tag.value == action.tag.value
                for action in indexed_actions["delete"]
            ) if "delete" in indexed_actions else False

            # If the tag will be deleted, skip the Database validation
            if not is_deleted_tag_value:
                # Validates if exists a tag with the same value on the Taxonomy
                taxonomy_tag = self.taxonomy.tag_set.get(value=self.tag.value)
                return ImportActionError(
                    action=self,
                    message=_(
                        "Duplicated tag value with tag in database (external_id={external_id})."
                    ).format(external_id=taxonomy_tag.external_id)
                )
        except Tag.DoesNotExist:
            pass

        # Validates value duplication on create actions
        action = self._search_action(
            indexed_actions,
            CreateTag.name,
            "value",
            self.tag.value,
        )

        if not action:
            # Validates value duplication on rename actions
            action = self._search_action(
                indexed_actions,
                RenameTag.name,
                "value",
                self.tag.value,
            )

        if not action:
            # Validates value duplication on rename_external_id actions
            action = self._search_action(
                indexed_actions,
                RenameTagExternalId.name,
                "value",
                self.tag.value,
            )

        if action:
            return ImportActionConflict(
                action=self,
                conflict_action_index=action.index,
                message=_("Duplicated tag value."),
            )

        return None

    @classmethod
    def _resolve_update_target(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> Tag | None:
        """
        Resolve the tag that RenameTag/UpdateParentTag would update by `id`,
        or None if this row isn't theirs to handle: a rename_external_id row
        (previous_id set and different from id, where `id` may belong to a
        different tag entirely), no match, or a tag already queued for
        deletion in this same import.
        """
        if tag.previous_id and tag.id != tag.previous_id:
            return None
        try:
            taxonomy_tag = taxonomy.tag_set.get(external_id=tag.id)
        except Tag.DoesNotExist:
            return None
        if indexed_actions and any(
            taxonomy_tag.external_id == action.tag.id
            for action in indexed_actions.get("delete", [])
        ):
            return None
        return taxonomy_tag


class CreateTag(ImportAction):
    """
    Action for create a Tag

    Action created if the tag doesn't exist on the database

    Validations:
    - Id duplicates with previous create actions.
    - Value duplicates with tags on the database.
    - Value duplicates with previous create and rename actions.
    - Parent validation. If the parent is in the database or created
      in previous actions.
    """

    name = "create"

    def __str__(self) -> str:
        return str(
            _(
                "Create a new tag with values "
                "(external_id={external_id}, value={value}, "
                "parent_id={parent_id})."
            ).format(external_id=self.tag.id, value=self.tag.value, parent_id=self.tag.parent_id)
        )

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action applies whenever the tag does not exist
        """
        if tag.previous_id and tag.id != tag.previous_id:
            return False
        try:
            taxonomy.tag_set.get(external_id=tag.id)
            return False
        except Tag.DoesNotExist:
            return True

    def _validate_id(self, indexed_actions) -> ImportActionError | None:
        """
        Check for id duplicates in previous create actions
        """
        action = self._search_action(indexed_actions, self.name, "id", self.tag.id)
        if action:
            return ImportActionConflict(
                action=self,
                conflict_action_index=action.index,
                message=_("Duplicated external_id tag."),
            )
        return None

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        Validates the creation action
        """
        errors = []

        # Duplicate id validation with previous create actions
        error = self._validate_id(indexed_actions)
        if error:
            errors.append(error)

        # Duplicate value validation
        error = self._validate_value(indexed_actions)
        if error:
            errors.append(error)

        # Parent validation
        if self.tag.parent_id:
            error = self._validate_parent(indexed_actions)
            if error:
                errors.append(error)

        return errors

    def execute(self) -> None:
        """
        Creates a Tag
        """
        self.taxonomy.add_tag(
            tag_value=self.tag.value,
            external_id=self.tag.id,
            parent_tag_value=self.taxonomy.tag_set.get(external_id=self.tag.parent_id).value
            if self.tag.parent_id is not None else None,
        )


class UpdateParentTag(ImportAction):
    """
    Action for update the parent of a Tag

    Action created if there is a change on the parent

    Validations:
    - Parent validation. If the parent is in the database
      or created in previous actions.
    """

    name = "update_parent"

    def __str__(self) -> str:
        taxonomy_tag = self._get_tag()

        description_str = _("Update the parent of {tag} from parent {old_parent} to {new_parent}").format(
            tag=taxonomy_tag.display_str(),
            old_parent=taxonomy_tag.parent.display_str() if taxonomy_tag.parent else None,
            new_parent=self.tag.parent_id,
        )

        return str(description_str)

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action applies whenever there is a change on the parent.

        See ImportAction._resolve_update_target for when this doesn't apply
        regardless of the parent change (queued for deletion, or a
        rename_external_id row).
        """
        taxonomy_tag = cls._resolve_update_target(taxonomy, tag, indexed_actions)
        if taxonomy_tag is None:
            return False
        return (
            taxonomy_tag.parent is not None
            and taxonomy_tag.parent.external_id != tag.parent_id
        ) or (taxonomy_tag.parent is None and tag.parent_id is not None)

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        Validates the update parent action
        """
        errors = []

        # Parent validation
        if self.tag.parent_id:
            error = self._validate_parent(indexed_actions)
            if error:
                errors.append(error)

        return errors

    def execute(self) -> None:
        """
        Updates the parent of a tag
        """
        taxonomy_tag = self._get_tag()
        parent = None
        if self.tag.parent_id:
            parent = self.taxonomy.tag_set.get(external_id=self.tag.parent_id)
        taxonomy_tag.parent = parent
        taxonomy_tag.save()


class RenameTag(ImportAction):
    """
    Action for rename a Tag

    Action created if there is a change on the tag value

    Validations:
    - Value duplicates with tags on the database.
    - Value duplicates with previous create and rename actions.
    """

    name = "rename"

    def __str__(self) -> str:
        taxonomy_tag = self._get_tag()
        description_str = _("Rename tag value of {tag} to '{new_value}'").format(
            tag=taxonomy_tag.display_str(),
            new_value=self.tag.value,
        )

        return str(description_str)

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action applies whenever there is a change on the tag value.

        See ImportAction._resolve_update_target for when this doesn't apply
        regardless of the value change (queued for deletion, or a
        rename_external_id row).
        """
        taxonomy_tag = cls._resolve_update_target(taxonomy, tag, indexed_actions)
        if taxonomy_tag is None:
            return False
        return taxonomy_tag.value != tag.value

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        Validates the rename action
        """
        errors = []

        # Duplicate value validation
        error = self._validate_value(indexed_actions)
        if error:
            errors.append(error)

        return errors

    def execute(self) -> None:
        """
        Rename a tag
        """
        taxonomy_tag = self._get_tag()
        taxonomy_tag.value = self.tag.value
        taxonomy_tag.save()


class RenameTagExternalId(ImportAction):
    """
    Action to rename an existing tag's external_id in place.

    Applies when a row's `previous_id` matches an existing tag and `id`
    differs from it. Preserves the tag's primary key and associations,
    instead of deleting the old tag and creating a new one.

    Validations:
    - previous_id must match an existing tag's external_id.
    - The new id must not collide with another existing tag or action.
    - Value duplicates, only if the value is changing.
    - Parent validation, if parent_id is set.
    """

    name = "rename_external_id"

    def __str__(self) -> str:
        return str(
            _(
                "Rename external_id of tag with previous_id={previous_id} to "
                "'{id}' (value={value}, parent_id={parent_id})."
            ).format(
                previous_id=self.tag.previous_id,
                id=self.tag.id,
                value=self.tag.value,
                parent_id=self.tag.parent_id,
            )
        )

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action applies whenever previous_id is set and differs from id
        """
        return bool(tag.previous_id) and tag.id != tag.previous_id

    def _validate_new_id(self, indexed_actions) -> ImportActionError | None:
        """
        Check that the new id doesn't collide with another existing tag or a
        prior create/rename action in this import. A tag already slated for
        delete or staged onto a placeholder id doesn't count as a collision,
        since both execute before this action (see _build_delete_actions,
        StageTagExternalIdForSwap).
        """
        is_freed_by_delete = any(
            self.tag.id == action.tag.id
            for action in indexed_actions["delete"]
        ) if "delete" in indexed_actions else False

        existing = self.taxonomy.tag_set.filter(external_id=self.tag.id).first()
        is_awaiting_placeholder_swap = existing is not None and any(
            existing.pk == action.target_pk
            for action in indexed_actions.get("stage_external_id", [])
        )

        if not is_freed_by_delete and not is_awaiting_placeholder_swap and existing is not None:
            return ImportActionError(
                action=self,
                message=_("A tag with external_id ({id}) already exists.").format(id=self.tag.id),
            )

        action = self._search_action(indexed_actions, CreateTag.name, "id", self.tag.id)
        if not action:
            action = self._search_action(indexed_actions, self.name, "id", self.tag.id)

        if action:
            return ImportActionConflict(
                action=self,
                conflict_action_index=action.index,
                message=_("Duplicated external_id tag."),
            )

        action = self._search_action(indexed_actions, self.name, "previous_id", self.tag.previous_id)
        if action:
            return ImportActionConflict(
                action=self,
                conflict_action_index=action.index,
                message=_("Duplicated previous_id tag."),
            )

        return None

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        Validates the rename_external_id action
        """
        errors = []

        try:
            matched_tag = self.taxonomy.tag_set.get(external_id=self.tag.previous_id)
        except Tag.DoesNotExist:
            matched_tag = None
            errors.append(
                ImportActionError(
                    action=self,
                    message=_(
                        "Unknown previous_id ({previous_id}). No tag with that "
                        "external_id exists in this taxonomy."
                    ).format(previous_id=self.tag.previous_id),
                )
            )

        error = self._validate_new_id(indexed_actions)
        if error:
            errors.append(error)

        if matched_tag is not None and matched_tag.value != self.tag.value:
            error = self._validate_value(indexed_actions)
            if error:
                errors.append(error)

        if self.tag.parent_id:
            error = self._validate_parent(indexed_actions)
            if error:
                errors.append(error)

        return errors

    def execute(self) -> None:
        """
        Renames the tag's external_id in place, and updates its value and parent.

        Looks up the target by pk, not by `previous_id`: a
        StageTagExternalIdForSwap action may have already moved it onto a
        placeholder id by execution time (see _build_staging_actions).
        """
        # validate() rejects an unmatched previous_id, and execute() never
        # runs when errors are present, so target_pk is always set here.
        assert self.target_pk is not None
        target = self.taxonomy.tag_set.get(pk=self.target_pk)
        target.external_id = self.tag.id
        target.value = self.tag.value
        target.parent = (
            self.taxonomy.tag_set.get(external_id=self.tag.parent_id)
            if self.tag.parent_id else None
        )
        target.save()


class StageTagExternalIdForSwap(ImportAction):
    """
    Action to move a tag off a contended external_id before another row in
    this import claims it.

    Synthesized by TagImportPlan._build_staging_actions, not read from a
    file row, when a tag's current external_id is another row's rename
    target. Needed for a swap or N-cycle of renames: (taxonomy,
    external_id) is a DB-level unique constraint enforced per-statement on
    every backend this project runs on, so nothing else gives such renames
    a valid execution order.
    """

    name = "stage_external_id"

    def __str__(self) -> str:
        return str(_("Stage tag (pk={target_pk}) off its current external_id.").format(target_pk=self.target_pk))

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action is an exception: synthesized in TagImportPlan.generate_actions.
        """
        return False

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        No validations necessary
        """
        return []

    def execute(self) -> None:
        """
        Moves the tag to a placeholder external_id, freeing its old one for
        another action in this same import to land on.
        """
        # _build_staging_actions only builds this action with a resolved
        # target_pk, so it's never None here.
        assert self.target_pk is not None
        placeholder = f"oel-import-staging:{uuid4().hex}"
        self.taxonomy.tag_set.filter(pk=self.target_pk).update(external_id=placeholder)


class DeleteTag(ImportAction):
    """
    Action for delete a Tag

    Action created if the action of the tag is 'delete'

    Does not require validations
    """

    def __str__(self) -> str:
        return str(_("Delete tag {tag}").format(tag=self.tag))

    name = "delete"

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        This action is an exception.
        These actions are created in `TagImportPlan.generate_actions` if `replace=True`
        """
        return False

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        No validations necessary
        """
        # TODO: Will it be necessary to check if this tag has children?
        return []

    def execute(self) -> None:
        """
        Delete a tag
        """
        try:
            self._get_tag().delete()
        except Tag.DoesNotExist:
            pass  # The tag may be already cascade deleted if the parent tag was deleted


class WithoutChanges(ImportAction):
    """
    Action when there is no change on the Tag

    Does not require validations
    """

    name = "without_changes"

    def __str__(self) -> str:
        return str(_("No changes needed for {tag}").format(tag=self.tag))

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag: TagItem, indexed_actions=None) -> bool:
        """
        No validations necessary
        """
        return False

    def validate(self, indexed_actions) -> list[ImportActionError]:
        """
        No validations necessary
        """
        return []

    def execute(self) -> None:
        """
        Do nothing
        """


# Register actions here in the order in which you want to check.
available_actions = [
    UpdateParentTag,
    RenameTag,
    RenameTagExternalId,
    CreateTag,
    StageTagExternalIdForSwap,
    DeleteTag,
    WithoutChanges,
]
