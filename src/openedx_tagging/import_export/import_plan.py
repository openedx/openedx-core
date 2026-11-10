"""
Classes and functions to create an import plan and execution.
"""
from __future__ import annotations

from typing import NamedTuple

from attrs import define
from django.db import transaction

from ..models import Tag, TagImportTask, Taxonomy
from ..models.base import TAXONOMY_MAX_DEPTH
from .actions import (
    DeleteTag,
    DetachTag,
    ImportAction,
    RenameTagExternalId,
    StageTagExternalIdForSwap,
    UpdateParentTag,
    WithoutChanges,
    available_actions,
)
from .exceptions import (
    DuplicateFinalIdError,
    ParentCycleError,
    StaleIdTargetedByPlainRowError,
    TagImportError,
    TagTooDeepError,
)


class ExistingTag(NamedTuple):
    """
    What the plan needs to know about a tag that exists before the import.
    """

    external_id: str
    value: str
    pk: int
    parent_pk: int | None


# Stands in for the pk of a tag the import creates. Never equal to a real pk or to None.
NEW_TAG = -1


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

    A tag's *pre-import id* is the external_id it holds before the import, and its
    *final id* is the one it holds after the import. They differ only for a tag the
    file renames. A row's ``parent_id`` names the final id of its parent. Three steps
    make parent changes work whatever the order of the rows in the file:

    - The plan computes the final tree, the tag tree as it will be after the import
      (``_end_state``), and rejects a parent cycle or a tree deeper than the limit there.
    - A DetachTag action moves every existing tag whose parent changes to the root
      before the row actions (``_build_detach_actions``). After that each tag is at or
      above its final depth, and none is an ancestor of its new parent, so no action can
      exceed the depth limit or build a cycle part-way through.
    - A row is built after the row that creates or renames-in its parent
      (``_order_rows_parent_first``), so the per-action parent validation and the
      execution both find the parent.

    "Row N" in an error is the N-th data row of the file, counting from 1: in a CSV file
    that is spreadsheet line N + 1, because the header is line 1.
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

    def _build_action(
        self, action_cls: type[ImportAction], tag: TagItem, target_pk: int | None = None, row: int | None = None,
    ):
        """
        Build an action with `tag`.

        Run action validation and adds the errors to the errors lists
        Add to the action list and the indexed actions
        """
        action = action_cls(self.taxonomy, tag, len(self.actions) + 1, target_pk=target_pk, row=row)

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

        Every kept child of a deleted tag is first promoted to the root, before
        any delete, because a delete cascades to the descendants.
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

        for tag in tags.values():
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
        new id to name it). A case-only rename is neither staged nor recorded:
        external_id is case-insensitive, so the tag keeps its id.

        indexed_actions["_renamed_to_ids"] holds the same new ids casefolded, built
        once here so that the per-row checks don't rebuild it.
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
            if tag.previous_id.casefold() == tag.id.casefold():
                continue  # A case-only rename keeps the id: nothing is vacated or contended.
            vacated_pks[target_pk] = tag.id
            if tag.previous_id.casefold() in target_ids:
                self._build_action(StageTagExternalIdForSwap, tag, target_pk=target_pk)
        self.indexed_actions["_vacated_pks"] = vacated_pks
        self.indexed_actions["_renamed_to_ids"] = {new_id.casefold() for new_id in vacated_pks.values()}

    def _existing_tags(self) -> dict[str, ExistingTag]:
        """
        Return the taxonomy's tags before the import, keyed by casefolded external_id. One query.
        """
        return {
            external_id.casefold(): ExistingTag(external_id, value, pk, parent_pk)
            for external_id, value, pk, parent_pk in self.taxonomy.tag_set.values_list(
                "external_id", "value", "pk", "parent_id",
            )
        }

    def _end_state(
        self, tags: list[TagItem], existing: dict[str, ExistingTag],
    ) -> tuple[dict[str, str | None], dict[str, int], dict[str, int]]:
        """
        Compute the final tree: the tag tree as it will be once the import is applied.

        Returns three dicts keyed by casefolded final id: ``parent_of`` (the parent's
        final id, or None for a root), ``pk_of`` (the pk of the tag that will hold the
        id, or NEW_TAG) and ``row_of`` (the position in ``tags`` of the id's row, for
        the ids that have one). Each row contributes its own id and parent_id. Must run
        after the delete and staging actions are built.
        """
        vacated_pks: dict[int, str] = self.indexed_actions["_vacated_pks"]
        deleted = {action.tag.id.casefold() for action in self.indexed_actions["delete"]}
        key_of_pk = {info.pk: key for key, info in existing.items()}
        parent_of: dict[str, str | None] = {}
        pk_of: dict[str, int] = {}
        # Final id -> the row's 0-based position in `tags`. Errors show position + 1 as "Row N".
        row_of: dict[str, int] = {}
        for position, tag in enumerate(tags):
            key = tag.id.casefold()
            if key in row_of:
                # A second row with the same final id is already an error from
                # _validate_no_duplicate_final_ids; keep the first.
                continue
            existing_tag = existing.get((tag.previous_id or tag.id).casefold())
            row_of[key] = position
            pk_of[key] = existing_tag.pk if existing_tag is not None else NEW_TAG
            parent_of[key] = tag.parent_id.casefold() if tag.parent_id else None
        in_file = {tag.id.casefold() for tag in tags} | {
            tag.previous_id.casefold() for tag in tags if tag.previous_id
        }
        for key, info in existing.items():
            if key in in_file or key in deleted:
                continue
            # What remains are existing tags the file never mentions and that are not
            # deleted, which only happens with replace=False. A renamed tag is not among
            # them: the loop above added it under its final id. These tags are kept
            # because they still count toward the depth and toward cycles. Each stays
            # under the same parent tag, recorded by that parent's final id.
            pk_of[key] = info.pk
            parent_of[key] = None
            if info.parent_pk is not None:
                parent_of[key] = vacated_pks.get(info.parent_pk, key_of_pk[info.parent_pk]).casefold()
        return parent_of, pk_of, row_of

    def _validate_hierarchy(
        self,
        tags: list[TagItem],
        existing: dict[str, ExistingTag],
        parent_of: dict[str, str | None],
        pk_of: dict[str, int],
        row_of: dict[str, int],
    ) -> None:
        """
        Reject a parent cycle, and a tree deeper than the taxonomy allows.

        A cycle is reported once, on its first row in the file. A cycle made only of
        new tags is left to ``_validate_parent``, which reports one of them as an
        unknown parent. A parent_id that is no tag of the final tree counts as a root
        here: it is unknown, which ``_validate_parent`` reports, or deleted without a
        row reclaiming it, and the delete phase promotes its children.
        """
        depth_of: dict[str, int] = {}
        unresolved: set[str] = set()  # on a cycle, or below one
        for start in parent_of:
            # Walk up from `start` through its ancestors. The walk stops at the top of the
            # tree (None), at a key whose depth is already known, at a key already known to
            # be on or below a cycle, or at a key already on `path`, which closes a cycle.
            # A parent_id that names no tag of the final tree counts as the top.
            path: list[str] = []
            node: str | None = start
            while node is not None and node not in depth_of and node not in unresolved and node not in path:
                path.append(node)
                node = parent_of.get(node)
                if node not in parent_of:
                    node = None
            if node is not None and node in path:
                cycle = path[path.index(node):]
                rows = [row_of[key] for key in cycle if key in row_of]
                if rows and any(pk_of[key] != NEW_TAG for key in cycle):
                    position = min(rows)
                    self.errors.append(ParentCycleError(position + 1, tags[position].parent_id or ""))
            if node is None or node in depth_of:
                depth = depth_of[node] if node is not None else -1
                for key in reversed(path):
                    depth += 1
                    depth_of[key] = depth
            else:
                # The walk closed a cycle, or reached a key on or below one: no key on
                # the path has a depth.
                unresolved.update(path)

        too_deep = [key for key, depth in depth_of.items() if depth > TAXONOMY_MAX_DEPTH]
        if too_deep:
            # Name the shallowest too-deep tag: the first one past the limit.
            key = min(too_deep, key=lambda key: (depth_of[key], row_of.get(key, len(tags)), key))
            tag_id = tags[row_of[key]].id if key in row_of else existing[key].external_id
            self.errors.append(TagTooDeepError(tag_id, TAXONOMY_MAX_DEPTH + 1))

    def _build_detach_actions(
        self, existing: dict[str, ExistingTag], parent_of: dict[str, str | None], pk_of: dict[str, int],
    ) -> None:
        """
        Add a DetachTag action for every existing tag whose parent after the import
        is a different tag than before.
        """
        by_pk = {info.pk: info for info in existing.values()}
        deleted_pks = {existing[action.tag.id.casefold()].pk for action in self.indexed_actions["delete"]}
        for key, pk in pk_of.items():
            if pk == NEW_TAG:
                continue  # A new tag has no parent to detach from.
            info = by_pk[pk]
            if info.parent_pk is None:
                continue  # A root tag is already at the root.
            if info.parent_pk in deleted_pks:
                continue  # The delete phase already promotes a kept child of a deleted tag.
            parent_key = parent_of[key]
            if parent_key is not None and parent_key not in pk_of:
                continue  # The parent_id names no tag of the final tree: left to _validate_parent.
            final_parent_pk = pk_of[parent_key] if parent_key is not None else None
            if final_parent_pk != info.parent_pk:
                self._build_action(DetachTag, TagItem(id=info.external_id, value=info.value), target_pk=pk)

    @staticmethod
    def _order_rows_parent_first(tags: list[TagItem], existing: dict[str, ExistingTag]) -> list[int]:
        """
        Return the positions in ``tags`` with each row after the row that creates or renames-in its parent, if any.

        The per-action parent validation and the execution look for a parent among the
        existing tags and the actions built so far, so this makes both independent of
        the row order. Rows keep their file order otherwise: a row is pulled forward only
        when a later row needs it, and a file whose parents already come before their
        children is left as it is.
        """
        defining_row: dict[str, int] = {}
        for position, tag in enumerate(tags):
            is_rename = bool(tag.previous_id) and tag.id != tag.previous_id
            if is_rename or tag.id.casefold() not in existing:
                defining_row.setdefault(tag.id.casefold(), position)
        ordered: list[int] = []
        placed: set[int] = set()
        for start in range(len(tags)):
            rows_to_place: list[int] = []
            position = start
            while position not in placed and position not in rows_to_place:
                rows_to_place.append(position)
                parent_id = tags[position].parent_id
                parent_row = defining_row.get(parent_id.casefold()) if parent_id else None
                if parent_row is None:
                    break
                position = parent_row
            for row in reversed(rows_to_place):
                placed.add(row)
                ordered.append(row)
        return ordered

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
        existing = self._existing_tags()

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

        # Reject a cycle or an overflow of the final tree, then detach every moved tag.
        parent_of, pk_of, row_of = self._end_state(tags, existing)
        self._validate_hierarchy(tags, existing, parent_of, pk_of, row_of)
        self._build_detach_actions(existing, parent_of, pk_of)

        for position in self._order_rows_parent_first(tags, existing):
            tag = tags[position]
            has_action = False

            # Check all available actions and add which ones should be executed
            for action_cls in available_actions:
                if action_cls.applies_for(self.taxonomy, tag, self.indexed_actions):
                    target_pk = (
                        self._resolve_rename_target_pk(tag)
                        if action_cls is RenameTagExternalId else None
                    )
                    self._build_action(action_cls, tag, target_pk=target_pk, row=position + 1)
                    has_action = True

            if not has_action:
                # If it doesn't find an action, a "without changes" is added
                self._build_action(WithoutChanges, tag, row=position + 1)

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
