"""
Actions for import tags
"""
from __future__ import annotations

from django.utils.translation import gettext as _

from ..models import Tag, Taxonomy
from .exceptions import ImportActionConflict, ImportActionError


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

    def __init__(self, taxonomy: Taxonomy, tag, index: int):
        self.taxonomy = taxonomy
        self.tag = tag
        self.index = index

    def __repr__(self) -> str:
        return str(_("Action {name} (index={index},id={id})").format(name=self.name, index=self.index, id=self.tag.id))

    def __str__(self) -> str:
        return self.__repr__()

    @classmethod
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
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
        search_value: str,
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
        """
        try:
            # Validates that the parent exists on the taxonomy
            self.taxonomy.tag_set.get(external_id=self.tag.parent_id)
        except Tag.DoesNotExist:
            # Or if the parent is created on previous actions
            if not self._search_action(
                indexed_actions, CreateTag.name, "id", self.tag.parent_id
            ):
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

        if action:
            return ImportActionConflict(
                action=self,
                conflict_action_index=action.index,
                message=_("Duplicated tag value."),
            )

        return None


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
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
        """
        This action applies whenever the tag does not exist
        """
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
        Tag.objects.create(
            taxonomy=self.taxonomy,
            parent=self.taxonomy.tag_set.get(external_id=self.tag.parent_id)
            if self.tag.parent_id is not None else None,
            value=self.tag.value,
            external_id=self.tag.id,
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
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
        """
        This action applies whenever there is a change on the parent
        """
        try:
            taxonomy_tag = taxonomy.tag_set.get(external_id=tag.id)
            return (
                taxonomy_tag.parent is not None
                and taxonomy_tag.parent.external_id != tag.parent_id
            ) or (taxonomy_tag.parent is None and tag.parent_id is not None)
        except Tag.DoesNotExist:
            return False

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
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
        """
        This action applies whenever there is a change on the tag value
        """
        try:
            taxonomy_tag = taxonomy.tag_set.get(external_id=tag.id)
            return taxonomy_tag.value != tag.value
        except Tag.DoesNotExist:
            return False

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
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
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
    def applies_for(cls, taxonomy: Taxonomy, tag) -> bool:
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
    CreateTag,
    DeleteTag,
    WithoutChanges,
]
