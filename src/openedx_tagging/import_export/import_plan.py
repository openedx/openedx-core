"""
Classes and functions to create an import plan and execution.
"""
from __future__ import annotations

from attrs import define
from django.db import transaction

from ..models import Tag, TagImportTask, Taxonomy
from .actions import (
    DeleteTag,
    ImportAction,
    RenameTagExternalId,
    StageTagExternalIdForSwap,
    UpdateParentTag,
    WithoutChanges,
    available_actions,
)
from .exceptions import DuplicateFinalIdError, StaleIdTargetedByPlainRowError, TagImportError


@define
class TagItem:
    """
    Tag representation on the tag import plan
    """

    id: str
    value: str
    index: int | None = 0
    parent_id: str | None = None
    previous_id: str | None = None

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
    errors: list[TagImportError]
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

    def _build_action(self, action_cls: type[ImportAction], tag: TagItem, target_pk: int | None = None):
        """
        Build an action with `tag`.

        Run action validation and adds the errors to the errors lists
        Add to the action list and the indexed actions
        """
        action = action_cls(self.taxonomy, tag, len(self.actions) + 1, target_pk=target_pk)

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

        The external_id is used for import and export.
        """
        return tag.external_id

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

    def _resolve_rename_target_pk(self, tag: TagItem) -> int | None:
        """
        Resolve the pk of the tag a RenameTagExternalId row targets, via its
        previous_id. Returns None if no such tag exists (an unmatched
        previous_id -- RenameTagExternalId.validate() already rejects this).
        """
        return self.taxonomy.tag_set.filter(external_id=tag.previous_id).values_list("pk", flat=True).first()

    def _validate_no_duplicate_final_ids(self, tags: list[TagItem]) -> None:
        """
        Reject two or more rows in the same import that claim the same
        final `id`, before staging or per-row action-building runs. This
        also catches a row colliding with a staged tag's target, since a
        tag is only ever staged because its external_id is already another
        row's target id.

        Without this, the outcome depended on row order: a silent
        overwrite of whichever row landed first, or an uncaught crash at
        execute time.

        Rows are identified by their 1-based position in `tags`, not
        `TagItem.index`: index is parser-assigned and optional, and left
        at its default for hand-built rows (e.g. in tests), while position
        is always defined.
        """
        positions_by_id: dict[str, list[int]] = {}
        display_id_by_key: dict[str, str] = {}
        for position, tag in enumerate(tags, start=1):
            key = tag.id.casefold()
            display_id_by_key.setdefault(key, tag.id)
            positions_by_id.setdefault(key, []).append(position)

        for key, positions in positions_by_id.items():
            if len(positions) > 1:
                self.errors.append(DuplicateFinalIdError(display_id_by_key[key], positions))

    def _validate_no_plain_row_targets_a_vacated_id(self, tags: list[TagItem]) -> None:
        """
        Reject a plain row (no previous_id) whose `id` matches another
        row's `previous_id` in the same import: that other row is renaming
        its tag away from that id, so a plain row landing on it would
        either resolve to the wrong tag or crash at execute time, depending
        on row order. A rename row landing on that same id is the
        already-supported swap/cycle case and is left alone here. The match is
        case-insensitive, like `external_id`'s own DB collation.
        """
        rename_source_rows: dict[str, int] = {}
        for position, tag in enumerate(tags, start=1):
            if tag.previous_id and tag.id != tag.previous_id:
                rename_source_rows.setdefault(tag.previous_id.casefold(), position)

        for position, tag in enumerate(tags, start=1):
            if bool(tag.previous_id) and tag.id != tag.previous_id:
                continue  # a rename row landing on that id is the already-supported swap case
            rename_row = rename_source_rows.get(tag.id.casefold())
            if rename_row is not None:
                self.errors.append(StaleIdTargetedByPlainRowError(tag.id, position, rename_row))

    def _build_staging_actions(self, tags: list[TagItem]) -> None:
        """
        Stage any tag whose current external_id is another rename row's
        target in this import, so renames never collide on external_id
        regardless of file order (see StageTagExternalIdForSwap).

        Also records every rename's resolved target pk in
        indexed_actions["_vacated_pks"] (pk -> the id it's renamed *to*), staged
        or not: once a tag is renamed away from an external_id, that id is stale
        for anyone still referencing it via the live database, even if nothing
        in this import reuses it (see _validate_parent, which uses the recorded
        new id to name it).
        """
        target_ids = {
            tag.id.casefold() for tag in tags
            if RenameTagExternalId.applies_for(self.taxonomy, tag)
        }
        vacated_pks: dict[int, str] = {}
        for tag in tags:
            if not RenameTagExternalId.applies_for(self.taxonomy, tag):
                continue
            # RenameTagExternalId.applies_for requires previous_id truthy.
            assert tag.previous_id is not None
            target_pk = self._resolve_rename_target_pk(tag)
            if target_pk is None:
                continue
            vacated_pks[target_pk] = tag.id
            is_case_only_rename = tag.previous_id.casefold() == tag.id.casefold()
            if not is_case_only_rename and tag.previous_id.casefold() in target_ids:
                self._build_action(StageTagExternalIdForSwap, tag, target_pk=target_pk)
        self.indexed_actions["_vacated_pks"] = vacated_pks

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

        # Reject two or more rows claiming the same final id outright,
        # before staging or per-row action-building runs (see
        # _validate_no_duplicate_final_ids).
        self._validate_no_duplicate_final_ids(tags)

        # Reject a plain row that targets an id another row is renaming
        # away from in this same import (see
        # _validate_no_plain_row_targets_a_vacated_id).
        self._validate_no_plain_row_targets_a_vacated_id(tags)

        tags_for_delete = {}

        if replace:
            tags_for_delete = {
                self._get_tag_id(tag): tag for tag in self.taxonomy.tag_set.all()
            }
            # external_id is case-insensitive in the DB, so both pops below
            # must match regardless of case too.
            delete_keys_by_fold = {key.casefold(): key for key in tags_for_delete}

            for tag in tags:
                # A rename row's `id` is the new target, not confirmation
                # that the tag currently holding that external_id should be
                # kept: only `previous_id` protects an existing tag from
                # this delete sweep in that case.
                is_rename = bool(tag.previous_id) and tag.id != tag.previous_id
                if not is_rename:
                    key = delete_keys_by_fold.get(tag.id.casefold())
                    if key is not None:
                        tags_for_delete.pop(key, None)
                if tag.previous_id:
                    key = delete_keys_by_fold.get(tag.previous_id.casefold())
                    if key is not None:
                        tags_for_delete.pop(key, None)

            # Delete all not readed tags
            self._build_delete_actions(tags_for_delete)

        # Stage tags whose external_id is contended by another rename row in
        # this same import, so a swap or an N-cycle of renames has a valid
        # execution order regardless of how the rows are ordered in the file.
        self._build_staging_actions(tags)

        for tag in tags:
            has_action = False

            # Check all available actions and add which ones should be executed
            for action_cls in available_actions:
                if action_cls.applies_for(self.taxonomy, tag, self.indexed_actions):
                    target_pk = (
                        self._resolve_rename_target_pk(tag)
                        if action_cls is RenameTagExternalId else None
                    )
                    self._build_action(action_cls, tag, target_pk=target_pk)
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
