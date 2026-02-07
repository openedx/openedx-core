"""
Test for import_plan functions
"""
import ddt  # type: ignore[import]
from django.test.testcases import TestCase

from openedx_tagging.import_export.actions import CreateTag
from openedx_tagging.import_export.exceptions import TagImportError
from openedx_tagging.import_export.import_plan import TagImportPlan, TagItem

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
            3,
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
                    'name': 'delete',
                    'id': 'tag_1',
                },
                {
                    'name': 'delete',
                    'id': 'tag_2',
                },
                {
                    'name': 'update_parent',
                    'id': 'tag_4',
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
            "#1: Create a new tag with values (external_id=tag_31, value=Tag 31, parent_id=None).\n"
            "#2: Create a new tag with values (external_id=tag_31, value=Tag 32, parent_id=None).\n"
            "#3: Rename tag value of <Tag> (tag_1 / Tag 1) to 'Tag 2'\n"
            "#4: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to tag_100\n"
            "#5: Create a new tag with values (external_id=tag_33, value=Tag 32, parent_id=None).\n"
            "#6: Update the parent of <Tag> (tag_2 / Tag 2) from parent "
            "<Tag> (tag_1 / Tag 1) to None\n"
            "#7: Rename tag value of <Tag> (tag_2 / Tag 2) to 'Tag 31'\n"
            "\nOutput errors\n"
            "--------------------------------\n"
            "Conflict with 'create' (#2) and action #1: Duplicated external_id tag.\n"
            "Action error in 'rename' (#3): Duplicated tag value with tag in database (external_id=tag_2).\n"
            "Action error in 'update_parent' (#4): Unknown parent tag (tag_100). "
            "You need to add parent before the child in your file.\n"
            "Conflict with 'create' (#5) and action #2: Duplicated tag value.\n"
            "Conflict with 'rename' (#7) and action #1: Duplicated tag value.\n"
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
            "#1: Create a new tag with values (external_id=tag_31, value=Tag 31, parent_id=None).\n"
            "#2: Create a new tag with values (external_id=tag_32, value=Tag 32, parent_id=tag_1).\n"
            "#3: Rename tag value of <Tag> (tag_2 / Tag 2) to 'Tag 2 v2'\n"
            "#4: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to tag_1\n"
            "#5: Rename tag value of <Tag> (tag_4 / Tag 4) to 'Tag 4 v2'\n"
            "#6: No changes needed for <TagItem> (tag_1 / Tag 1)\n"
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
            "#1: Delete tag <TagItem> (tag_1 / Tag 1)\n"
            "#2: Delete tag <TagItem> (tag_2 / Tag 2)\n"
            "#3: Update the parent of <Tag> (tag_4 / Tag 4) from parent "
            "<Tag> (tag_3 / Tag 3) to None\n"
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
