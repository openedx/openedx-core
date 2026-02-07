"""
Classes and functions to create an import plan and execution.
"""
from __future__ import annotations

from attrs import define
from django.db import transaction

from ..models import Tag, TagImportTask, Taxonomy
from .actions import DeleteTag, ImportAction, UpdateParentTag, WithoutChanges, available_actions
from .exceptions import ImportActionError


@define
class TagItem:
    """
    Tag representation on the tag import plan
    """

    id: str
    value: str
    index: int | None = 0
    parent_id: str | None = None

    def __str__(self):
        """
        User-facing string representation of a Tag.
        """
        if self.id:
            return f"<{self.__class__.__name__}> ({self.id} / {self.value})"
        return f"<{self.__class__.__name__}> ({self.value})"


class TagImportPlan:
    """
    Class with functions to build an import plan and excute the plan
    """

    actions: list[ImportAction]
    errors: list[ImportActionError]
    indexed_actions: dict
    actions_dict: dict
    taxonomy: Taxonomy

    def __init__(self, taxonomy: Taxonomy):
        self.actions = []
        self.errors = []
        self.taxonomy = taxonomy
        self.actions_dict = {}
        self._init_indexed_actions()

    def _init_indexed_actions(self):
        """
        Initialize the `indexed_actions` dict
        """
        self.indexed_actions = {}
        for action in available_actions:
            self.indexed_actions[action.name] = []

    def _build_action(self, action_cls: type[ImportAction], tag: TagItem):
        """
        Build an action with `tag`.

        Run action validation and adds the errors to the errors lists
        Add to the action list and the indexed actions
        """
        action = action_cls(self.taxonomy, tag, len(self.actions) + 1)

        # We validate if there are no inconsistencies when executing this action
        self.errors.extend(action.validate(self.indexed_actions))

        # Add action
        self.actions.append(action)

        # Index the actions for search
        self.indexed_actions[action.name].append(action)

    def _search_parent_update(
        self,
        child_external_id,
        parent_external_id,
    ):
        """
        Checks if there is a parent update in a child
        """
        for action in self.indexed_actions["update_parent"]:
            if (
                child_external_id == action.tag.id
                and parent_external_id != action.tag.parent_id
            ):
                return True

        return False

    def _get_tag_id(self, tag: Tag) -> str:
        """
        Get the id used on the Tag model.

        By default, the external_id is used for import and export,
        but there are cases where taxonomies are created without external_id.
        In those cases the tag id is used
        """
        if tag.external_id:
            return tag.external_id
        return str(tag.id)

    def _build_delete_actions(self, tags: dict):
        """
        Adds delete actions for `tags`
        """
        for tag in tags.values():
            for child in tag.children.all():
                # Verify if there is not a parent update before
                if not self._search_parent_update(self._get_tag_id(child), self._get_tag_id(tag)):
                    # Change parent to avoid delete childs
                    if self._get_tag_id(child) not in tags:
                        # Only update parent if the child is not going to be deleted
                        self._build_action(
                            UpdateParentTag,
                            TagItem(
                                id=child.external_id,
                                value=child.value,
                                parent_id=None,
                            ),
                        )

            # Delete action
            self._build_action(
                DeleteTag,
                TagItem(
                    id=tag.external_id,
                    value=tag.value,
                ),
            )

    def generate_actions(
        self,
        tags: list[TagItem],
        replace=False,
    ):
        """
        Reads each tag and generates the corresponding actions.

        Validates each action and create respective errors
        If `replace` is True, then creates the delete action for tags
        that are in the existing taxonomy but not the new tags list.

        TODO: Join/reduce actions. Ex. A tag may have no changes,
        but then its parent needs to be updated because its parent is deleted.
        Those two actions should be merged.
        """
        self.actions.clear()
        self.errors.clear()
        self._init_indexed_actions()
        tags_for_delete = {}

        if replace:
            tags_for_delete = {
                self._get_tag_id(tag): tag for tag in self.taxonomy.tag_set.all()
            }

            for tag in tags:
                if tag.id in tags_for_delete:
                    tags_for_delete.pop(tag.id)

            # Delete all not readed tags
            self._build_delete_actions(tags_for_delete)

        for tag in tags:
            has_action = False

            # Check all available actions and add which ones should be executed
            for action_cls in available_actions:
                if action_cls.applies_for(self.taxonomy, tag):
                    self._build_action(action_cls, tag)
                    has_action = True

            if not has_action:
                # If it doesn't find an action, a "without changes" is added
                self._build_action(WithoutChanges, tag)

    def plan(self) -> str:
        """
        Returns an string with the plan and errors
        """
        result = (
            f"Import plan for {self.taxonomy.name}\n"
            "--------------------------------\n"
        )
        for action in self.actions:
            result += f"#{action.index}: {str(action)}\n"

        if self.errors:
            result += "\nOutput errors\n" "--------------------------------\n"
            for error in self.errors:
                result += f"{str(error)}\n"

        return result

    @transaction.atomic()
    def execute(self, task: TagImportTask | None = None):
        """
        Executes each action

        If task is set, creates logs for each action
        """
        if self.errors:
            return
        for action in self.actions:
            # Avoid to save each log because it is slow and costs a lot in memory
            # It is necessary to save at the end.
            if task:
                task.add_log(f"#{action.index}: {str(action)} [Started]", save=False)
            action.execute()
            if task:
                task.add_log("Success", save=False)
        if task:
            task.save()
