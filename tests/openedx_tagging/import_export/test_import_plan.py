"""
Test for import_plan functions
"""
import ddt  # type: ignore[import]
from django.db import transaction
from django.test.testcases import TestCase

from openedx_tagging.import_export.actions import CreateTag
from openedx_tagging.import_export.exceptions import TagImportError
from openedx_tagging.import_export.import_plan import TagImportPlan, TagItem
from openedx_tagging.models import Tag, TagImportTask, Taxonomy

from .test_actions import TestImportActionMixin


@ddt.ddt
class TestTagImportPlan(TestImportActionMixin, TestCase):
    """
    Test for import plan functions
    """

    def setUp(self) -> None:
        super().setUp()
        self.import_plan = TagImportPlan(self.taxonomy)

    def test_tag_import_error(self) -> None:
        message = "Error message"
        expected_repr = f"TagImportError({message})"
        error = TagImportError(message)
        assert str(error) == message
        assert repr(error) == expected_repr

    @ddt.data(
        ('tag_10', 1),  # Test invalid
        ('tag_30', 0),  # Test valid
    )
    @ddt.unpack
    def test_build_action(self, tag_id: str, errors_expected: int):
        self.import_plan.indexed_actions = self.indexed_actions
        self.import_plan._build_action(  # pylint: disable=protected-access
            CreateTag,
            TagItem(
                id=tag_id,
                value='_',
                index=100
            )
        )
        assert len(self.import_plan.errors) == errors_expected
        assert len(self.import_plan.actions) == 1
        assert self.import_plan.actions[0].name == 'create'
        assert self.import_plan.indexed_actions['create'][1].tag.id == tag_id

    def test_build_delete_actions(self) -> None:
        tags = {
            tag.external_id: tag
            for tag in self.taxonomy.tag_set.exclude(pk=25)
        }
        # Clear other actions to only have the delete ones
        self.import_plan.actions.clear()

        self.import_plan._build_delete_actions(tags)  # pylint: disable=protected-access
        assert len(self.import_plan.errors) == 0

        # Check actions in order
        # #1 Delete 'tag_1'
        assert self.import_plan.actions[0].name == 'delete'
        assert self.import_plan.actions[0].tag.id == 'tag_1'
        # #2 Delete 'tag_2'
        assert self.import_plan.actions[1].name == 'delete'
        assert self.import_plan.actions[1].tag.id == 'tag_2'
        # #3 Delete 'tag_3'
        assert self.import_plan.actions[2].name == 'delete'
        assert self.import_plan.actions[2].tag.id == 'tag_3'

    @ddt.data(
        # Test valid actions
        (
            [
                {
                    'id': 'tag_31',
                    'value': 'Tag 31',
                },
                {
                    'id': 'tag_32',
                    'value': 'Tag 32',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_2',
                    'value': 'Tag 2 v2',
                    'parent_id': 'tag_1'
                },
                {
                    'id': 'tag_4',
                    'value': 'Tag 4 v2',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_1',
                    'value': 'Tag 1',
                },
            ],
            False,
            0,
            [
                {
                    'name': 'detach',
                    'id': 'tag_4'
                },
                {
                    'name': 'create',
                    'id': 'tag_31'
                },
                {
                    'name': 'create',
                    'id': 'tag_32'
                },
                {
                    'name': 'rename',
                    'id': 'tag_2'
                },
                {
                    'name': 'update_parent',
                    'id': 'tag_4'
                },
                {
                    'name': 'rename',
                    'id': 'tag_4'
                },
                {
                    'name': 'without_changes',
                    'id': 'tag_1'
                },
            ]
        ),
        # Test with errors in actions
        (
            [
                {
                    'id': 'tag_31',
                    'value': 'Tag 31',
                },
                {
                    'id': 'tag_31',
                    'value': 'Tag 32',
                },
                {
                    'id': 'tag_1',
                    'value': 'Tag 2',
                },
                {
                    'id': 'tag_4',
                    'value': 'Tag 4',
                    'parent_id': 'tag_100',
                },
            ],
            False,
            4,
            [
                {
                    'name': 'create',
                    'id': 'tag_31',
                },
                {
                    'name': 'create',
                    'id': 'tag_31',
                },
                {
                    'name': 'rename',
                    'id': 'tag_1',
                },
                {
                    'name': 'update_parent',
                    'id': 'tag_4',
                }
            ]
        ),
        # Test with deletes (replace=True)
        (
            [
                {
                    'id': 'tag_4',
                    'value': 'Tag 4',
                    'parent_id': 'tag_3',
                },
            ],
            True,
            0,
            [
                {
                    'name': 'update_parent',
                    'id': 'tag_4',
                },
                {
                    'name': 'delete',
                    'id': 'tag_1',
                },
                {
                    'name': 'delete',
                    'id': 'tag_2',
                },
                {
                    'name': 'delete',
                    'id': 'tag_3',
                },
                {
                    'name': 'without_changes',
                    'id': 'tag_4',
                },
            ]
        )
    )
    @ddt.unpack
    def test_generate_actions(self, tags, replace, expected_errors, expected_actions):
        tags = [TagItem(**tag) for tag in tags]
        self.import_plan.generate_actions(tags=tags, replace=replace)
        assert len(self.import_plan.errors) == expected_errors
        assert len(self.import_plan.actions) == len(expected_actions)

        for index, action in enumerate(expected_actions):
            assert self.import_plan.actions[index].name == action['name']
            assert self.import_plan.actions[index].tag.id == action['id']
            assert self.import_plan.actions[index].index == index + 1

    @ddt.data(
        # Testing plan with errors
        (
            [
                {
                    'id': 'tag_31',
                    'value': 'Tag 31',
                },
                {
                    'id': 'tag_31',
                    'value': 'Tag 32',
                },
                {
                    'id': 'tag_1',
                    'value': 'Tag 2',
                },
                {
                    'id': 'tag_4',
                    'value': 'Tag 4',
                    'parent_id': 'tag_100',
                },
                {
                    'id': 'tag_33',
                    'value': 'Tag 32',
                },
                {
                    'id': 'tag_2',
                    'value': 'Tag 31',
                },
            ],
            False,
            "Import plan for Import Taxonomy Test\n"
            "--------------------------------\n"
            "#1: Move tag <TagItem> (tag_2 / Tag 2) to the root first, so that the parent changes "
            "in this file can run in any order.\n"
            "#2: Create a new tag with values (external_id=tag_31, value=Tag 31, parent_id=None).\n"
            "#3: Create a new tag with values (external_id=tag_31, value=Tag 32, parent_id=None).\n"
            "#4: Rename tag value of <Tag> (tag_1 / Tag 1) to 'Tag 2'\n"
            "#5: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to tag_100\n"
            "#6: Create a new tag with values (external_id=tag_33, value=Tag 32, parent_id=None).\n"
            "#7: Update the parent of <Tag> (tag_2 / Tag 2) from parent "
            "<Tag> (tag_1 / Tag 1) to None\n"
            "#8: Rename tag value of <Tag> (tag_2 / Tag 2) to 'Tag 31'\n"
            "\nOutput errors\n"
            "--------------------------------\n"
            "Duplicate id (tag_31): file rows 1, 2 all claim it as their final id. "
            "Each row's id must be unique within a single import.\n"
            "Conflict with 'create' (#3) and action #2: Duplicated external_id tag.\n"
            "Action error in 'rename' (#4): Duplicated tag value with tag in database (external_id=tag_2).\n"
            "Action error in 'update_parent' (#5): Unknown parent tag (tag_100). "
            "Add a row for the parent to your file.\n"
            "Conflict with 'create' (#6) and action #3: Duplicated tag value.\n"
            "Conflict with 'rename' (#8) and action #2: Duplicated tag value.\n"
        ),
        # Testing valid plan
        (
            [
                {
                    'id': 'tag_31',
                    'value': 'Tag 31',
                },
                {
                    'id': 'tag_32',
                    'value': 'Tag 32',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_2',
                    'value': 'Tag 2 v2',
                    'parent_id': 'tag_1'
                },
                {
                    'id': 'tag_4',
                    'value': 'Tag 4 v2',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_1',
                    'value': 'Tag 1',
                },
            ],
            False,
            "Import plan for Import Taxonomy Test\n"
            "--------------------------------\n"
            "#1: Move tag <TagItem> (tag_4 / Tag 4) to the root first, so that the parent changes "
            "in this file can run in any order.\n"
            "#2: Create a new tag with values (external_id=tag_31, value=Tag 31, parent_id=None).\n"
            "#3: Create a new tag with values (external_id=tag_32, value=Tag 32, parent_id=tag_1).\n"
            "#4: Rename tag value of <Tag> (tag_2 / Tag 2) to 'Tag 2 v2'\n"
            "#5: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to tag_1\n"
            "#6: Rename tag value of <Tag> (tag_4 / Tag 4) to 'Tag 4 v2'\n"
            "#7: No changes needed for <TagItem> (tag_1 / Tag 1)\n"
        ),
        # Testing deletes (replace=True)
        (
            [
                {
                    'id': 'tag_4',
                    'value': 'Tag 4',
                    'parent_id': 'tag_3',
                },
            ],
            True,
            "Import plan for Import Taxonomy Test\n"
            "--------------------------------\n"
            "#1: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to None\n"
            "#2: Delete tag <TagItem> (tag_1 / Tag 1)\n"
            "#3: Delete tag <TagItem> (tag_2 / Tag 2)\n"
            "#4: Delete tag <TagItem> (tag_3 / Tag 3)\n"
            "#5: No changes needed for <TagItem> (tag_4 / Tag 4)\n"
        ),
    )
    @ddt.unpack
    def test_plan(self, tags, replace, expected):
        """
        Test the output of plan() function

        It has been decided to verify the output exactly to detect
        any error when printing this information that the user is going to read.
        """
        tags = [TagItem(**tag) for tag in tags]
        self.import_plan.generate_actions(tags=tags, replace=replace)
        plan = self.import_plan.plan()
        assert plan == expected

    @ddt.data(
        # Testing all actions
        (
            [
                {
                    'id': 'tag_31',
                    'value': 'Tag 31',
                },
                {
                    'id': 'tag_32',
                    'value': 'Tag 32',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_2',
                    'value': 'Tag 2 v2',
                    'parent_id': 'tag_1'
                },
                {
                    'id': 'tag_4',
                    'value': 'Tag 4 v2',
                    'parent_id': 'tag_1',
                },
                {
                    'id': 'tag_1',
                    'value': 'Tag 1',
                },
            ],
            False,
        ),
        # Testing deletes (replace=True)
        (
            [
                {
                    'id': 'tag_4',
                    'value': 'Tag 4',
                    'parent_id': 'tag_3',
                },
            ],
            True,
        ),
    )
    @ddt.unpack
    def test_execute(self, tags, replace):
        tags = [TagItem(**tag) for tag in tags]
        self.import_plan.generate_actions(tags=tags, replace=replace)
        self.import_plan.execute()
        tag_external_ids = []
        for tag_item in tags:
            # This checks any creation
            tag = self.taxonomy.tag_set.get(external_id=tag_item.id)

            # Checks any rename
            assert tag.value == tag_item.value

            # Checks any parent update
            if not replace:
                if not tag_item.parent_id:
                    assert tag.parent is None
                else:
                    assert tag.parent.external_id == tag_item.parent_id

            tag_external_ids.append(tag_item.id)

        if replace:
            # Checks deletions checking that exists the updated tags
            external_ids = list(self.taxonomy.tag_set.values_list("external_id", flat=True))
            assert tag_external_ids == external_ids

    def test_generate_actions_rename_external_id(self) -> None:
        tags = [
            TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.errors), 0)
        self.assertEqual(len(self.import_plan.actions), 1)
        self.assertEqual(self.import_plan.actions[0].name, 'rename_external_id')
        self.assertEqual(self.import_plan.actions[0].tag.id, 'tag_50')

    def test_generate_actions_rename_external_id_case_only_skips_staging(self) -> None:
        """
        A case-only rename (id differs from previous_id only in case) must not
        stage itself via StageTagExternalIdForSwap: there is no other row
        contending for the id, so the swap is unnecessary and its plan message
        ("temporarily move tag off its external_id...") would be misleading.
        """
        tags = [
            TagItem(id='TAG_1', value='Tag 1', previous_id='tag_1'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.errors), 0)
        self.assertEqual(len(self.import_plan.indexed_actions['stage_external_id']), 0)
        self.assertEqual(len(self.import_plan.indexed_actions['rename_external_id']), 1)

    def test_generate_actions_rename_external_id_replace_skips_delete(self) -> None:
        # tag_1 is renamed to tag_50 (previous_id='tag_1'); under replace=True
        # its old id must not be swept up in the delete pass, since it is the
        # same underlying tag, not a removed one.
        tags = [
            TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_2', value='Tag 2'),
            TagItem(id='tag_3', value='Tag 3'),
            TagItem(id='tag_4', value='Tag 4', parent_id='tag_3'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=True)
        self.assertEqual(len(self.import_plan.errors), 0)
        delete_targets = [
            action.tag.id for action in self.import_plan.actions if action.name == 'delete'
        ]
        self.assertNotIn('tag_1', delete_targets)

    def test_generate_actions_rename_external_id_replace_skips_delete_case_different_previous_id(self) -> None:
        """
        Regression: previous_id='TAG_1' must still protect the existing
        tag_1 tag from the replace-mode delete sweep even though it differs
        from the stored external_id only in case, since external_id is a
        case-insensitive DB field. Before the fix, the exact-case pop missed
        this tag, queuing it for deletion and then crashing the rename's own
        execute() with Tag.DoesNotExist.
        """
        tags = [
            TagItem(id='tag_50', value='Tag 1', previous_id='TAG_1'),
            TagItem(id='tag_2', value='Tag 2'),
            TagItem(id='tag_3', value='Tag 3'),
            TagItem(id='tag_4', value='Tag 4', parent_id='tag_3'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=True)
        self.assertEqual(len(self.import_plan.errors), 0)
        delete_targets = [
            action.tag.id for action in self.import_plan.actions if action.name == 'delete'
        ]
        self.assertNotIn('tag_1', delete_targets)

    def test_generate_actions_plain_row_replace_skips_delete_case_different_id(self) -> None:
        """
        Regression: a plain row (no previous_id) with id='TAG_1' must still
        protect the existing tag_1 tag from the replace-mode delete sweep
        even though it differs from the stored external_id only in case.
        Before the fix, the exact-case `tag.id in tags_for_delete` check
        missed this tag, queuing it for deletion and then crashing
        tag_4's own update_parent action (an explicit re-parent onto
        "tag_1") once tag_1 no longer existed.
        """
        tags = [
            TagItem(id='TAG_1', value='Tag 1'),
            TagItem(id='tag_2', value='Tag 2', parent_id='tag_1'),
            TagItem(id='tag_3', value='Tag 3'),
            TagItem(id='tag_4', value='Tag 4', parent_id='tag_1'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=True)
        self.assertEqual(len(self.import_plan.errors), 0)
        delete_targets = [
            action.tag.id for action in self.import_plan.actions if action.name == 'delete'
        ]
        self.assertNotIn('tag_1', delete_targets)

    def test_generate_actions_rename_external_id_value_collision_with_create(self) -> None:
        """
        Regression: a value collision between a `RenameTagExternalId` action
        and a later `CreateTag` action in the same import must be caught at
        validate time, not silently pass through to `execute()` and hit the
        database's `unique_together(taxonomy, value)` constraint.
        """
        tags = [
            TagItem(id='tag_50', value='Shared', previous_id='tag_1'),
            TagItem(id='tag_60', value='Shared'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.errors), 1)
        self.assertIn("Duplicated tag value", str(self.import_plan.errors[0]))

    def test_error_in_execute(self):
        created_tag = 'tag_31'
        tags = [
            TagItem(
                id=created_tag,
                value='Tag 31'
            ),  # Valid tag (creation)
            TagItem(
                id='tag_32',
                value='Tag 31'
            ),  # Invalid
        ]
        self.import_plan.generate_actions(tags=tags)
        assert not self.taxonomy.tag_set.filter(external_id=created_tag).exists()
        assert not self.import_plan.execute()
        assert not self.taxonomy.tag_set.filter(external_id=created_tag).exists()

    def test_generate_actions_swap_stages_and_renames(self) -> None:
        """
        A 2-tag swap (tag_1 <-> tag_3, both root tags) has no valid plain
        execution order, since (taxonomy, external_id) is unique and
        enforced per-statement: each tag must be staged through a
        placeholder id first.
        """
        tags = [
            TagItem(id='tag_3', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_1', value='Tag 3', previous_id='tag_3'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])
        self.assertEqual(len(self.import_plan.indexed_actions['stage_external_id']), 2)
        self.assertEqual(len(self.import_plan.indexed_actions['rename_external_id']), 2)
        self.assertEqual(self.import_plan.indexed_actions['rename'], [])
        self.assertEqual(self.import_plan.indexed_actions['update_parent'], [])

    def test_generate_actions_three_cycle_stages_all(self) -> None:
        """
        A 3-cycle (tag_1 -> tag_2 -> tag_3 -> tag_1) is staged the same way
        as a 2-tag swap: every tag in the cycle is contended by another row
        in the same import, so all three are staged first.
        """
        tags = [
            TagItem(id='tag_2', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_3', value='Tag 2', previous_id='tag_2'),
            TagItem(id='tag_1', value='Tag 3', previous_id='tag_3'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])
        self.assertEqual(len(self.import_plan.indexed_actions['stage_external_id']), 3)
        self.assertEqual(len(self.import_plan.indexed_actions['rename_external_id']), 3)

    def test_generate_actions_chain_stages_only_contended_tags(self) -> None:
        """
        A chain tag_2 -> tag_3 -> tag_4 -> tag_90 (fresh, uncontended).
        Only tag_3 and tag_4 are contended (their current id is another
        row's target); tag_2's current id is nobody's target, so it isn't
        staged.
        """
        tag_2_pk = self.taxonomy.tag_set.get(external_id='tag_2').pk
        tag_3_pk = self.taxonomy.tag_set.get(external_id='tag_3').pk
        tag_4_pk = self.taxonomy.tag_set.get(external_id='tag_4').pk

        tags = [
            TagItem(id='tag_3', value='Tag 2', previous_id='tag_2'),
            TagItem(id='tag_4', value='Tag 3', previous_id='tag_3'),
            TagItem(id='tag_90', value='Tag 4', previous_id='tag_4'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])
        staged_pks = {
            action.target_pk for action in self.import_plan.indexed_actions['stage_external_id']
        }
        self.assertEqual(staged_pks, {tag_3_pk, tag_4_pk})
        self.assertNotIn(tag_2_pk, staged_pks)
        self.assertEqual(len(self.import_plan.indexed_actions['rename_external_id']), 3)

    @ddt.data(
        ('tag_1', False),
        ('TAG_1', False),  # a case variant of the stale id
        ('TAG_1', True),  # the child row first
    )
    @ddt.unpack
    def test_generate_actions_parent_id_stale_after_plain_rename_rejected(
        self, child_parent_id: str, child_first: bool,
    ) -> None:
        """
        Regression: tag_1 is renamed to tag_50 and nothing reuses "tag_1"
        (a plain, non-contended rename, never staged). A different row's
        parent_id references the now-stale "tag_1" -- must be rejected,
        since no tag will hold that external_id after the import.
        """
        rename = TagItem(id='tag_50', value='Tag 1', previous_id='tag_1')
        child = TagItem(id='tag_60', value='Tag 60', parent_id=child_parent_id)
        row = 1 if child_first else 2
        self.import_plan.generate_actions(tags=[child, rename] if child_first else [rename, child], replace=False)
        # One error, without the hint for a swap: no other row takes the id.
        self.assertEqual(
            [str(error) for error in self.import_plan.errors],
            [
                f"Action error in 'create' (#{row}): Row {row}: Parent tag {child_parent_id} is renamed "
                "to tag_50 in this file; use tag_50 as the parent_id."
            ],
        )

    def test_generate_actions_parent_id_new_id_after_earlier_rename_accepted(self) -> None:
        """
        Same rename as above (tag_1 -> tag_50), but the other row's
        parent_id references the *new* id "tag_50" instead of the stale old
        one, and the rename row comes first in the file: this must be
        accepted, same convention as referencing a newly-created parent.
        """
        tags = [
            TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_60', value='Tag 60', parent_id='tag_50'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])

    @ddt.data(
        (
            [
                TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
                TagItem(id='tag_2', value='Tag 2', parent_id='tag_1'),
            ],
            False,
            "Action error in 'update_parent' (#2): ",
        ),
        # A new child: replace=True deletes tag_3 and tag_4 first, so its action is #4, not #2.
        (
            [
                TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
                TagItem(id='tag_60', value='Tag 60', parent_id='tag_1'),
                TagItem(id='tag_2', value='Tag 2', parent_id='tag_50'),
            ],
            True,
            "Action error in 'create' (#4): ",
        ),
    )
    @ddt.unpack
    def test_generate_actions_existing_child_keeps_stale_parent_id_rejected(
        self, tags: list[TagItem], replace: bool, action_prefix: str,
    ) -> None:
        self.import_plan.generate_actions(tags=tags, replace=replace)
        self.assertEqual(
            [str(error) for error in self.import_plan.errors],
            [
                f"{action_prefix}"
                "Row 2: Parent tag tag_1 is renamed to tag_50 in this file; use tag_50 as the parent_id."
            ],
        )

    def test_generate_actions_swap_renamed_child_naming_own_parent_stale_id_rejected(self) -> None:
        tags = [
            TagItem(id='tag_3', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_1', value='Tag 3', previous_id='tag_3'),
            TagItem(id='tag_20', value='Tag 2', previous_id='tag_2', parent_id='tag_1'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        # The staging and detach actions come first, so the action number is not the row number.
        self.assertEqual(
            [str(error) for error in self.import_plan.errors],
            [
                "Action error in 'rename_external_id' (#6): Row 3: Parent tag tag_1 is renamed to tag_3 in this "
                "file; use tag_3 as the parent_id. To move under the tag that takes the id tag_1 in this file "
                "instead, import the id changes and the move as two separate files."
            ],
        )

    @ddt.data('tag_1', 'TAG_1')
    def test_generate_actions_case_only_parent_rename_keeps_child(self, child_parent_id: str) -> None:
        """
        Renaming tag_1 to TAG_1 only changes its capitalization. external_id is
        case-insensitive, so the id still names the same tag after the import,
        and a tag_2 row naming it in either spelling is not a stale reference.
        """
        tags = [
            TagItem(id='TAG_1', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_2', value='Tag 2', parent_id=child_parent_id),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])

    @ddt.data(
        # A new child listed before its new parent.
        (
            [
                TagItem(id='new_child', value='New child', parent_id='new_parent'),
                TagItem(id='new_parent', value='New parent'),
            ],
            ['new_parent', 'new_child'],
        ),
        # A row naming a parent's new id, spelled in another case, listed before the rename.
        (
            [
                TagItem(id='tag_60', value='Tag 60', parent_id='TAG_50'),
                TagItem(id='tag_50', value='Tag 1', previous_id='tag_1'),
            ],
            ['tag_50', 'tag_60'],
        ),
        # Parents already before their children: the plan keeps the file order.
        (
            [
                TagItem(id='new_parent', value='New parent'),
                TagItem(id='tag_4', value='Tag 4 v2', parent_id='tag_3'),
                TagItem(id='new_child', value='New child', parent_id='new_parent'),
            ],
            ['new_parent', 'tag_4', 'new_child'],
        ),
    )
    @ddt.unpack
    def test_generate_actions_orders_each_row_after_the_row_defining_its_parent(
        self, tags: list[TagItem], expected_order: list[str],
    ) -> None:
        """
        A row is built after the row that creates or renames-in its parent, so a
        file may list rows in any order. Rows are only pulled forward when needed.
        """
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.errors, [])
        self.assertEqual([action.tag.id for action in self.import_plan.actions], expected_order)

    def _plan_for_tree(self, tree: dict[str, str | None]) -> TagImportPlan:
        """
        Create a taxonomy holding ``tree`` (id -> parent id, parents listed first) and
        return a plan for it. The tag values are the ids.
        """
        taxonomy = Taxonomy.objects.create(name='Tree taxonomy')
        tags: dict[str, Tag] = {}
        for tag_id, parent_id in tree.items():
            tags[tag_id] = Tag.objects.create(
                taxonomy=taxonomy, value=tag_id, external_id=tag_id,
                parent=tags[parent_id] if parent_id else None,
            )
        return TagImportPlan(taxonomy)

    @staticmethod
    def _tree(taxonomy: Taxonomy) -> dict[str, str | None]:
        """
        The taxonomy's tags as id -> parent id.
        """
        return {tag.external_id: tag.parent.external_id if tag.parent else None for tag in taxonomy.tag_set.all()}

    SIX_LEVELS: dict[str, str | None] = {
        'level_1': None, 'level_2': 'level_1', 'level_3': 'level_2',
        'level_4': 'level_3', 'level_5': 'level_4', 'level_6': 'level_5',
    }

    @ddt.data(
        # A three-level chain is turned upside down.
        (
            {'top': None, 'middle': 'top', 'bottom': 'middle'},
            [
                TagItem(id='top', value='top', parent_id='middle'),
                TagItem(id='middle', value='middle', parent_id='bottom'),
                TagItem(id='bottom', value='bottom'),
            ],
            {'bottom': None, 'middle': 'bottom', 'top': 'middle'},
        ),
        # A root moves under its former grandchild, while the middle tag becomes a root.
        (
            {'top': None, 'middle': 'top', 'bottom': 'middle'},
            [
                TagItem(id='top', value='top', parent_id='bottom'),
                TagItem(id='middle', value='middle'),
                TagItem(id='bottom', value='bottom', parent_id='middle'),
            ],
            {'middle': None, 'bottom': 'middle', 'top': 'bottom'},
        ),
        # A parent and its child swap places.
        (
            {'parent': None, 'child': 'parent'},
            [TagItem(id='parent', value='parent', parent_id='child'), TagItem(id='child', value='child')],
            {'child': None, 'parent': 'child'},
        ),
        # The ancestry is inverted through a new tag: new_root must leave old_root's
        # subtree before old_root moves under new_middle.
        (
            {'old_root': None, 'sibling': 'old_root', 'new_root': 'old_root'},
            [
                TagItem(id='old_root', value='old_root', parent_id='new_middle'),
                TagItem(id='sibling', value='sibling', parent_id='new_middle'),
                TagItem(id='new_middle', value='new_middle', parent_id='new_root'),
                TagItem(id='new_root', value='new_root'),
            ],
            {'new_root': None, 'new_middle': 'new_root', 'old_root': 'new_middle', 'sibling': 'new_middle'},
        ),
        # A deep tag moves to the root while new tags are created under it: created
        # first, the new grandchild would be at a seventh level.
        (
            {'level_1': None, 'level_2': 'level_1', 'level_3': 'level_2', 'level_4': 'level_3', 'deep': 'level_4'},
            [
                TagItem(id='new_grandchild', value='new_grandchild', parent_id='new_child'),
                TagItem(id='new_child', value='new_child', parent_id='deep'),
                TagItem(id='deep', value='deep'),
            ],
            {
                'level_1': None, 'level_2': 'level_1', 'level_3': 'level_2', 'level_4': 'level_3',
                'deep': None, 'new_child': 'deep', 'new_grandchild': 'new_child',
            },
        ),
        # A subtree moves under a tag that itself moves up: attached first, the
        # grandchild would be at a seventh level.
        (
            {
                'chain_1': None, 'chain_2': 'chain_1', 'chain_3': 'chain_2', 'moved_up': 'chain_3',
                'subtree': None, 'subtree_child': 'subtree', 'subtree_grandchild': 'subtree_child',
            },
            [
                TagItem(id='subtree', value='subtree', parent_id='moved_up'),
                TagItem(id='moved_up', value='moved_up'),
            ],
            {
                'chain_1': None, 'chain_2': 'chain_1', 'chain_3': 'chain_2', 'moved_up': None,
                'subtree': 'moved_up', 'subtree_child': 'subtree', 'subtree_grandchild': 'subtree_child',
            },
        ),
    )
    @ddt.unpack
    def test_execute_parent_moves_whose_steps_depend_on_each_other(
        self, tree: dict[str, str | None], rows: list[TagItem], expected: dict[str, str | None],
    ) -> None:
        """
        Restructures that fail when their actions run in file order, because an
        intermediate step would build a cycle or a tree too deep, succeed: a
        detach action moves every tag whose parent changes to the root before the
        row actions. ``rows`` lists the worst order for each case, which also runs
        reversed and in one mixed order. Omitted tags keep their parent (replace=False).
        """
        plan = self._plan_for_tree(tree)
        pks = dict(plan.taxonomy.tag_set.values_list('external_id', 'pk'))
        forward = tuple(range(len(rows)))
        for order in dict.fromkeys([forward, forward[::-1], forward[1::2] + forward[0::2]]):
            with self.subTest(order=order), transaction.atomic():
                plan.generate_actions(tags=[rows[i] for i in order], replace=False)
                self.assertEqual([str(error) for error in plan.errors], [])
                names = [action.name for action in plan.actions]
                self.assertIn('detach', names)
                # All detaches run before any row action. This holds here because these cases have
                # no renames or deletes, which are built before the detaches.
                self.assertEqual(names[:names.count('detach')], ['detach'] * names.count('detach'))
                plan.execute()
                self.assertEqual(self._tree(plan.taxonomy), expected)
                # Moved tags keep their database rows.
                for tag_id, pk in pks.items():
                    self.assertEqual(plan.taxonomy.tag_set.get(external_id=tag_id).pk, pk)
                transaction.set_rollback(True)

    def test_execute_kept_tag_under_deleted_ancestors_survives(self) -> None:
        """
        With replace=True, a kept tag whose parent and grandparent are deleted is
        promoted to the root before the first delete, so the cascade does not
        remove it, and it keeps its database row.
        """
        plan = self._plan_for_tree({'deleted_root': None, 'deleted_child': 'deleted_root', 'kept': 'deleted_child'})
        kept_pk = plan.taxonomy.tag_set.get(external_id='kept').pk
        plan.generate_actions(tags=[TagItem(id='kept', value='kept')], replace=True)
        self.assertEqual(plan.errors, [])
        names = [action.name for action in plan.actions]
        self.assertLess(names.index('update_parent'), names.index('delete'))
        self.assertNotIn('detach', names)  # The promotion already moves the tag to the root.
        plan.execute()
        self.assertEqual(self._tree(plan.taxonomy), {'kept': None})
        self.assertEqual(plan.taxonomy.tag_set.get(external_id='kept').pk, kept_pk)

    def test_execute_logs_the_old_parent_of_a_moved_tag(self) -> None:
        # The plan is not logged first, so the description must already be built when the
        # detach action moves tag_2 to the root.
        task = TagImportTask.create(self.taxonomy)
        self.import_plan.generate_actions(tags=[TagItem(id='tag_2', value='Tag 2', parent_id='tag_3')])
        self.import_plan.execute(task)
        self.assertEqual(self.taxonomy.tag_set.get(external_id='tag_2').parent.external_id, 'tag_3')
        self.assertIn(
            "Update the parent of <Tag> (tag_2 / Tag 2) from parent <Tag> (tag_1 / Tag 1) to tag_3 [Started]",
            task.log,
        )

    @ddt.data(
        # tag_1 under its own child tag_2.
        ([TagItem(id='tag_1', value='Tag 1', parent_id='tag_2')], False, "Row 1's parent_id (tag_2)"),
        # A tag under itself.
        ([TagItem(id='tag_2', value='Tag 2', parent_id='tag_2')], False, "Row 1's parent_id (tag_2)"),
        # tag_3 takes the id of the deleted tag_1 and names that id as its parent.
        (
            [TagItem(id='tag_1', value='Tag 3', previous_id='tag_3', parent_id='tag_1')],
            True,
            "Row 1's parent_id (tag_1)",
        ),
        # Two renamed tags, on ids the import frees, name each other.
        (
            [
                TagItem(id='tag_4', value='Tag 4', parent_id='tag_2'),
                TagItem(id='tag_2', value='Tag 1', previous_id='tag_1', parent_id='tag_3'),
                TagItem(id='tag_3', value='Tag 2', previous_id='tag_2', parent_id='tag_2'),
            ],
            True,
            "Row 2's parent_id (tag_3)",
        ),
        # The same two renamed tags, in the other row order.
        (
            [
                TagItem(id='tag_4', value='Tag 4', parent_id='tag_2'),
                TagItem(id='tag_3', value='Tag 2', previous_id='tag_2', parent_id='tag_2'),
                TagItem(id='tag_2', value='Tag 1', previous_id='tag_1', parent_id='tag_3'),
            ],
            True,
            "Row 2's parent_id (tag_2)",
        ),
    )
    @ddt.unpack
    def test_generate_actions_parent_cycle_rejected_once(
        self, tags: list[TagItem], replace: bool, expected_prefix: str,
    ) -> None:
        """
        A parent_id that makes a tag its own ancestor after the import is rejected
        with one error, on the cycle's first row in the file.
        """
        self.import_plan.generate_actions(tags=tags, replace=replace)
        messages = [str(error) for error in self.import_plan.errors]
        self.assertEqual(len(messages), 1, messages)
        self.assertEqual(messages[0], f"{expected_prefix} creates a parent cycle.")

    def test_generate_actions_long_reversed_chain_terminates(self) -> None:
        """
        A 1100-row chain of new tags listed child first is ordered and checked
        without recursion, so it neither hits the recursion limit nor crashes:
        every row is planned, and the chain is rejected as too deep.
        """
        tags = [
            TagItem(id=f'n{i}', value=f'N{i}', parent_id=f'n{i - 1}' if i else None)
            for i in reversed(range(1100))
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.actions), 1100)
        self.assertEqual(self.import_plan.actions[0].tag.id, 'n0')
        self.assertEqual(
            [str(error) for error in self.import_plan.errors],
            ["This import would leave tag n6 more than 6 levels deep."],
        )

    def test_generate_actions_cycle_of_new_tags_reports_an_unknown_parent_once(self) -> None:
        """
        New tags that name each other as parent get the existing "Unknown parent"
        error for one of them, and no cycle error on top.
        """
        tags = [
            TagItem(id='new_a', value='New A', parent_id='new_b'),
            TagItem(id='new_b', value='New B', parent_id='new_a'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        messages = [str(error) for error in self.import_plan.errors]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("Unknown parent tag", messages[0])

    FOUR_LEVELS: dict[str, str | None] = {
        'other_1': None, 'other_2': 'other_1', 'other_3': 'other_2', 'other_4': 'other_3',
    }

    @ddt.data(
        # A new tag under the sixth level.
        (TagItem(id='level_7', value='level_7', parent_id='level_6'), 'level_7'),
        # A four-level chain moved under the fourth level: other_3 and other_4 end up
        # at the seventh and eighth level, although neither has a row.
        (TagItem(id='other_1', value='other_1', parent_id='level_4'), 'other_3'),
    )
    @ddt.unpack
    def test_generate_actions_import_too_deep_rejected(self, row: TagItem, named_tag: str) -> None:
        """
        An import whose result would leave a tag deeper than the taxonomy allows is
        rejected at the plan step, with one error naming a tag that would be too deep.
        """
        plan = self._plan_for_tree({**self.SIX_LEVELS, **self.FOUR_LEVELS})
        plan.generate_actions(tags=[row], replace=False)
        self.assertEqual(
            [str(error) for error in plan.errors],
            [f"This import would leave tag {named_tag} more than 6 levels deep."],
        )

    def test_generate_actions_genuine_collision_not_staged(self) -> None:
        """
        A rename targeting an id held by an unrelated tag that is not
        itself being renamed or deleted in this import is a real collision,
        not a staging candidate: the tag holding tag_2 is not a party to
        any rename row in this file.
        """
        tags = [
            TagItem(id='tag_2', value='Tag 1', previous_id='tag_1'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(self.import_plan.indexed_actions['stage_external_id'], [])
        self.assertEqual(len(self.import_plan.errors), 1)
        self.assertIn("already exists", str(self.import_plan.errors[0]))

    def test_generate_actions_rejects_duplicate_final_id(self) -> None:
        """
        Two rows can't claim the same final id: a tag_1<->tag_3 swap plus
        an unrelated third row also targeting id=tag_1 is ambiguous.
        Reject the whole import outright, naming both offending rows by
        position, instead of letting swap-staging silently treat the
        collision as valid (see DuplicateFinalIdError).

        The unrelated third row (plain, id=tag_1) also independently
        matches _validate_no_plain_row_targets_a_vacated_id, since tag_1 is
        also the first row's previous_id: both checks catch a real,
        distinct problem with that same row, so two errors are expected.
        """
        tags = [
            TagItem(id='tag_3', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_1', value='Tag 3', previous_id='tag_3'),
            TagItem(id='tag_1', value='Something Else Entirely'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.errors), 2)
        error = str(self.import_plan.errors[0])
        self.assertIn("tag_1", error)
        self.assertIn("file rows 2, 3", error)

    def test_generate_actions_rejects_duplicate_final_id_regardless_of_order(self) -> None:
        """
        Same collision as test_generate_actions_rejects_duplicate_final_id,
        but with the unrelated row moved to the front of the file: the
        rejection doesn't depend on row order.
        """
        tags = [
            TagItem(id='tag_1', value='Something Else Entirely'),
            TagItem(id='tag_3', value='Tag 1', previous_id='tag_1'),
            TagItem(id='tag_1', value='Tag 3', previous_id='tag_3'),
        ]
        self.import_plan.generate_actions(tags=tags, replace=False)
        self.assertEqual(len(self.import_plan.errors), 2)
        error = str(self.import_plan.errors[0])
        self.assertIn("tag_1", error)
        self.assertIn("file rows 1, 3", error)
