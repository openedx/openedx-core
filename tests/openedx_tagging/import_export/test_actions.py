"""
Tests for actions
"""
from __future__ import annotations

import ddt  # type: ignore[import]
from django.core.exceptions import ValidationError
from django.test.testcases import TestCase

from openedx_tagging.import_export.actions import (
    CreateTag,
    DeleteTag,
    ImportAction,
    RenameTag,
    RenameTagExternalId,
    UpdateParentTag,
    WithoutChanges,
)
from openedx_tagging.import_export.import_plan import TagItem
from openedx_tagging.models import Tag
from openedx_tagging.models.base import TAXONOMY_MAX_DEPTH

from .mixins import TestImportExportMixin


class TestImportActionMixin(TestImportExportMixin):
    """
    Mixin for import action tests
    """
    indexed_actions: dict[str, list[ImportAction]]

    def setUp(self) -> None:  # Note: we must specify '-> None' to opt in to type checking
        super().setUp()
        self.indexed_actions = {
            'create': [
                CreateTag(
                    taxonomy=self.taxonomy,
                    tag=TagItem(
                        id='tag_10',
                        value='Tag 10',
                        index=0
                    ),
                    index=0,
                )
            ],
            'rename': [
                RenameTag(
                    taxonomy=self.taxonomy,
                    tag=TagItem(
                        id='tag_11',
                        value='Tag 11',
                        index=1
                    ),
                    index=1,
                )
            ],
            'rename_external_id': [],
        }


@ddt.ddt
class TestImportAction(TestImportActionMixin, TestCase):
    """
    Test for general function of the ImportAction class
    """

    def test_not_implemented_functions(self):
        with self.assertRaises(NotImplementedError):
            ImportAction.applies_for(None, None)
        action = ImportAction(None, None, None)
        with self.assertRaises(NotImplementedError):
            action.validate(None)
        with self.assertRaises(NotImplementedError):
            action.execute()

    def test_str(self) -> None:
        expected = "Action import_action (index=100,id=tag_1)"
        action = ImportAction(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_1',
                value='value',
            ),
            index=100,
        )
        assert str(action) == expected

    @ddt.data(
        ('create', 'id', 'tag_10', True),
        ('rename', 'value', 'Tag 11', True),
        ('rename', 'id', 'tag_10', False),
        ('create', 'value', 'Tag 11', False),
    )
    @ddt.unpack
    def test_search_action(self, action_name, attr, search_value, expected):
        import_action = ImportAction(self.taxonomy, None, None)
        action = import_action._search_action(  # pylint: disable=protected-access
            self.indexed_actions,
            action_name,
            attr,
            search_value,
        )
        if expected:
            self.assertEqual(getattr(action.tag, attr), search_value)
        else:
            self.assertIsNone(action)

    @ddt.data(
        ('tag_1', True),
        ('tag_10', True),
        ('tag_100', False),
    )
    @ddt.unpack
    def test_validate_parent(self, parent_id: str, expected: bool):
        action = ImportAction(
            self.taxonomy,
            TagItem(
                id='tag_110',
                value='_',
                parent_id=parent_id,
                index=100,
            ),
            index=100,
        )
        error = action._validate_parent(self.indexed_actions)  # pylint: disable=protected-access
        if expected:
            self.assertIsNone(error)
        else:
            self.assertEqual(
                str(error),
                (
                    "Action error in 'import_action' (#100): "
                    "Unknown parent tag (tag_100). "
                    "You need to add parent before the child in your file."
                )
            )

    def test_validate_parent_with_rename_external_id_action(self) -> None:
        """
        Regression: a parent referenced by external_id that doesn't exist in
        the DB yet, but is being renamed-in via a `RenameTagExternalId`
        action earlier in the same import, must validate as a known parent.
        """
        indexed_actions = dict(self.indexed_actions)
        indexed_actions['rename_external_id'] = [
            RenameTagExternalId(
                taxonomy=self.taxonomy,
                tag=TagItem(id='tag_60', value='Tag 60', previous_id='tag_3', index=1),
                index=1,
            )
        ]
        action = ImportAction(
            self.taxonomy,
            TagItem(
                id='tag_110',
                value='_',
                parent_id='tag_60',
                index=100,
            ),
            index=100,
        )
        error = action._validate_parent(indexed_actions)  # pylint: disable=protected-access
        self.assertIsNone(error)

    @ddt.data(
        (
            'Tag 1',
            (
                "Action error in 'import_action' (#100): "
                "Duplicated tag value with tag in database (external_id=tag_1)."
            )
        ),
        (
            'Tag 10',
            (
                "Conflict with 'import_action' (#100) "
                "and action #0: Duplicated tag value."
            )
        ),
        (
            'Tag 11',
            (
                "Conflict with 'import_action' (#100) "
                "and action #1: Duplicated tag value."
            )
        ),
        ('Tag 20', None)
    )
    @ddt.unpack
    def test_validate_value(self, value: str, expected: str | None):
        action = ImportAction(
            self.taxonomy,
            TagItem(
                id='tag_110',
                value=value,
                index=100
            ),
            index=100,
        )
        error = action._validate_value(self.indexed_actions)  # pylint: disable=protected-access
        if not expected:
            self.assertIsNone(error)
        else:
            self.assertEqual(str(error), expected)

    def test_validate_value_with_rename_external_id_action(self) -> None:
        """
        Regression: a value collision with a `RenameTagExternalId` action
        already queued in the same import must be caught, not only
        collisions with `create`/`rename` actions.
        """
        indexed_actions = dict(self.indexed_actions)
        indexed_actions['rename_external_id'] = [
            RenameTagExternalId(
                taxonomy=self.taxonomy,
                tag=TagItem(id='tag_60', value='Shared', previous_id='tag_3', index=1),
                index=1,
            )
        ]
        action = ImportAction(
            self.taxonomy,
            TagItem(
                id='tag_110',
                value='Shared',
                index=100,
            ),
            index=100,
        )
        error = action._validate_value(indexed_actions)  # pylint: disable=protected-access
        self.assertEqual(
            str(error),
            "Conflict with 'import_action' (#100) and action #1: Duplicated tag value."
        )


@ddt.ddt
class TestCreateTag(TestImportActionMixin, TestCase):
    """
    Test for 'create' action
    """

    @ddt.data(
        ('tag_1', False),
        ('tag_100', True),
    )
    @ddt.unpack
    def test_applies_for(self, tag_id: str, expected: bool):
        result = CreateTag.applies_for(
            self.taxonomy,
            TagItem(
                id=tag_id,
                value='_',
                index=100,
            )
        )
        self.assertEqual(result, expected)

    def test_applies_for_previous_id_guard(self) -> None:
        """
        A row with a `previous_id` that differs from `id` is a rename
        candidate, not a create: `RenameTagExternalId` should handle it
        even though no tag exists yet with the new id.
        """
        result = CreateTag.applies_for(
            self.taxonomy,
            TagItem(
                id='tag_100',
                value='_',
                previous_id='tag_99',
                index=100,
            )
        )
        self.assertFalse(result)

    @ddt.data(
        ('tag_10', False),
        ('tag_100', True),
    )
    @ddt.unpack
    def test_validate_id(self, tag_id: str, expected: bool):
        action = CreateTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value='_',
                index=100,
            ),
            index=100
        )
        error = action._validate_id(self.indexed_actions)  # pylint: disable=protected-access
        if expected:
            self.assertIsNone(error)
        else:
            self.assertEqual(
                str(error),
                (
                    "Conflict with 'create' (#100) "
                    "and action #0: Duplicated external_id tag."
                )
            )

    @ddt.data(
        ('tag_10', "Tag 20", None, 1),  # Invalid tag id
        ('tag_20', "Tag 10", None, 1),  # Invalid value,
        ('tag_20', "Tag 20", 'tag_100', 1),  # Invalid parent id,
        ('tag_10', "Tag 10", None, 2),  # Invalid tag id and value,
        ('tag_10', "Tag 10", 'tag_100', 3),  # Invalid tag id, value and parent,
        ('tag_20', "Tag 20", 'tag_1', 0),  # Valid
    )
    @ddt.unpack
    def test_validate(self, tag_id: str, tag_value: str, parent_id: str | None, expected: bool):
        action = CreateTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value=tag_value,
                index=100,
                parent_id=parent_id
            ),
            index=100
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), expected)

    def test_execute_exceeds_max_depth(self) -> None:
        """Importing a tag that would exceed TAXONOMY_MAX_DEPTH raises ValidationError and fails."""
        # Build a chain of tags at depths 0 through TAXONOMY_MAX_DEPTH (the maximum allowed depth).
        parent = None
        for i in range(TAXONOMY_MAX_DEPTH + 1):
            parent = Tag.objects.create(
                taxonomy=self.taxonomy,
                value=f"Deep Tag {i}",
                parent=parent,
                external_id=f"deep_tag_{i}",
            )
        # parent is now at depth TAXONOMY_MAX_DEPTH; a child would be at depth TAXONOMY_MAX_DEPTH + 1.
        action = CreateTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=f"deep_tag_{TAXONOMY_MAX_DEPTH + 1}",
                value=f"Deep Tag {TAXONOMY_MAX_DEPTH + 1}",
                parent_id=f"deep_tag_{TAXONOMY_MAX_DEPTH}",
            ),
            index=100,
        )
        with self.assertRaises(ValidationError):
            action.execute()
        self.assertFalse(
            self.taxonomy.tag_set.filter(external_id=f"deep_tag_{TAXONOMY_MAX_DEPTH + 1}").exists()
        )

    @ddt.data(
        ('tag_30', 'Tag 30', None),  # No parent
        ('tag_31', 'Tag 31', 'tag_3'),  # With parent
    )
    @ddt.unpack
    def test_execute(self, tag_id: str, value: str, parent_id: str | None):
        tag = TagItem(
            id=tag_id,
            value=value,
            parent_id=parent_id,
        )
        action = CreateTag(
            self.taxonomy,
            tag,
            index=100,
        )
        with self.assertRaises(Tag.DoesNotExist):
            self.taxonomy.tag_set.get(external_id=tag_id)
        action.execute()
        tag_obj = self.taxonomy.tag_set.get(external_id=tag_id)
        assert tag_obj.value == value
        if parent_id:
            assert tag_obj.parent.external_id == parent_id
        else:
            assert tag_obj.parent is None


@ddt.ddt
class TestUpdateParentTag(TestImportActionMixin, TestCase):
    """
    Test for 'update_parent' action
    """

    @ddt.data(
        (
            "tag_4",
            "tag_3",
            (
                "Update the parent of <Tag> (tag_4 / Tag 4) "
                "from parent <Tag> (tag_3 / Tag 3) to tag_3"
            )
        ),
        (
            "tag_3",
            "tag_2",
            (
                "Update the parent of <Tag> (tag_3 / Tag 3) "
                "from parent None to tag_2"
            )
        ),
    )
    @ddt.unpack
    def test_str(self, tag_id: str, parent_id: str, expected: str):
        tag_item = TagItem(
            id=tag_id,
            value='_',
            parent_id=parent_id,
        )
        action = UpdateParentTag(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        assert str(action) == expected

    def test_str_no_external_id(self):
        tag_1 = Tag(
            value="Tag 5",
            taxonomy=self.taxonomy,
        )
        tag_1.save()
        tag_2 = Tag(
            value="Tag 6",
            taxonomy=self.taxonomy,
            parent=tag_1,
        )
        tag_2.save()
        tag_item = TagItem(
            id=None,
            value='Tag 6',
            parent_id='tag_3',
        )
        action = UpdateParentTag(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        expected = (
            "Update the parent of <Tag> (Tag 6) "
            "from parent <Tag> (Tag 5) to tag_3"
        )
        assert str(action) == expected

    @ddt.data(
        ('tag_100', None, False),  # Tag doesn't exist on database
        ('tag_2', 'tag_1', False),  # Parent don't change
        ('tag_2', 'tag_3', True),  # Valid
        ('tag_1', None, False),  # Both parent id are None
        ('tag_1', 'tag_3', True),  # Valid
    )
    @ddt.unpack
    def test_applies_for(self, tag_id: str, parent_id: str | None, expected: bool):
        result = UpdateParentTag.applies_for(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value='_',
                parent_id=parent_id,
                index=100
            )
        )
        self.assertEqual(result, expected)

    @ddt.data(
        ('tag_2', 'tag_30', 1),  # Invalid parent
        ('tag_2', None, 0),  # Without parent
        ('tag_2', 'tag_10', 0),  # Valid
    )
    @ddt.unpack
    def test_validate(self, tag_id: str, parent_id: str | None, expected: int):
        action = UpdateParentTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value='_',
                parent_id=parent_id,
            ),
            index=100
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), expected)

    @ddt.data(
        ('tag_4', 'tag_2'),  # Change parent
        ('tag_4', None),  # Set parent as None
        ('tag_3', 'tag_1'),  # Add parent
    )
    @ddt.unpack
    def test_execute(self, tag_id, parent_id):
        tag_item = TagItem(
            id=tag_id,
            value='_',
            parent_id=parent_id,
        )
        action = UpdateParentTag(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        tag = self.taxonomy.tag_set.get(external_id=tag_id)
        if tag.parent:
            assert tag.parent.external_id != parent_id
        action.execute()
        tag = self.taxonomy.tag_set.get(external_id=tag_id)
        if not parent_id:
            assert tag.parent is None
        else:
            assert tag.parent.external_id == parent_id


@ddt.ddt
class TestRenameTag(TestImportActionMixin, TestCase):
    """
    Test for 'rename' action
    """

    @ddt.data(
        ('tag_10', 'value', False),  # Tag doesn't exist on database
        ('tag_1', 'Tag 1', False),  # Same value
        ('tag_1', 'Tag 1 v2', True),  # Valid
    )
    @ddt.unpack
    def test_applies_for(self, tag_id: str, value: str, expected: bool):
        result = RenameTag.applies_for(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value=value,
                index=100,
            )
        )
        self.assertEqual(result, expected)

    @ddt.data(
        ('Tag 2', 1),  # There is a tag with the same value on database
        ('Tag 10', 1),  # There is a tag with the same value on create action
        ('Tag 11', 1),  # There is a tag with the same value on rename action
        ('Tag 12', 0),  # Valid
    )
    @ddt.unpack
    def test_validate(self, value: str, expected: int):
        action = RenameTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_1',
                value=value,
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), expected)

    def test_execute(self) -> None:
        tag_id = 'tag_1'
        value = 'Tag 1 V2'
        tag_item = TagItem(
            id=tag_id,
            value=value,
        )
        action = RenameTag(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        tag = self.taxonomy.tag_set.get(external_id=tag_id)
        assert tag.value != value
        action.execute()
        tag = self.taxonomy.tag_set.get(external_id=tag_id)
        assert tag.value == value


@ddt.ddt
class TestRenameTagExternalId(TestImportActionMixin, TestCase):
    """
    Test for 'rename_external_id' action
    """

    @ddt.data(
        (None, 'tag_50', False),  # No previous_id
        ('tag_1', 'tag_1', False),  # previous_id == id
        ('tag_1', 'tag_50', True),  # Valid rename
    )
    @ddt.unpack
    def test_applies_for(self, previous_id: str | None, tag_id: str, expected: bool):
        result = RenameTagExternalId.applies_for(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id=tag_id,
                value='_',
                previous_id=previous_id,
                index=100,
            )
        )
        self.assertEqual(result, expected)

    def test_validate_unmatched_previous_id(self) -> None:
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_50',
                value='Tag 50',
                previous_id='tag_100',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), 1)
        self.assertIn("Unknown previous_id (tag_100)", str(errors[0]))

    def test_validate_new_id_collides_with_db_tag(self) -> None:
        # previous_id matches tag_1, but the new id (tag_2) already belongs
        # to a different tag in the same taxonomy.
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_2',
                value='Tag 1',
                previous_id='tag_1',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), 1)
        self.assertIn("already exists", str(errors[0]))

    def test_validate_new_id_collides_with_create_action(self) -> None:
        # The new id (tag_10) matches a pending 'create' action from
        # self.indexed_actions (see TestImportActionMixin.setUp).
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_10',
                value='Tag 1',
                previous_id='tag_1',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), 1)
        self.assertIn("Duplicated external_id tag", str(errors[0]))

    def test_validate_new_id_collides_with_prior_rename_external_id_action(self) -> None:
        indexed_actions = dict(self.indexed_actions)
        indexed_actions['rename_external_id'] = [
            RenameTagExternalId(
                taxonomy=self.taxonomy,
                tag=TagItem(id='tag_60', value='Tag 60', previous_id='tag_3', index=1),
                index=1,
            )
        ]
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_60',
                value='Tag 1',
                previous_id='tag_1',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(indexed_actions)
        self.assertEqual(len(errors), 1)
        self.assertIn("Duplicated external_id tag", str(errors[0]))

    def test_validate_no_error_when_value_unchanged(self) -> None:
        # The row's value matches tag_1's current value, so _validate_value's
        # duplicate check is skipped, and nothing else is wrong.
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_50',
                value='Tag 1',
                previous_id='tag_1',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(errors, [])

    def test_validate_parent(self) -> None:
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='tag_50',
                value='Tag 1',
                previous_id='tag_1',
                parent_id='tag_100',
                index=100,
            ),
            index=100,
        )
        errors = action.validate(self.indexed_actions)
        self.assertEqual(len(errors), 1)
        self.assertIn("Unknown parent tag (tag_100)", str(errors[0]))

    def test_execute(self) -> None:
        tag = self.taxonomy.tag_set.get(external_id='tag_1')
        pk = tag.pk
        tag_item = TagItem(
            id='tag_50',
            value='Tag 50',
            previous_id='tag_1',
            parent_id='tag_3',
        )
        action = RenameTagExternalId(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        action.execute()
        tag.refresh_from_db()
        self.assertEqual(tag.pk, pk)
        self.assertEqual(tag.external_id, 'tag_50')
        self.assertEqual(tag.value, 'Tag 50')
        self.assertEqual(tag.parent.external_id, 'tag_3')


class TestDeleteTag(TestImportActionMixin, TestCase):
    """
    Test for 'delete' action
    """

    def test_applies_for(self) -> None:
        assert not DeleteTag.applies_for(self.taxonomy, None)

    def test_validate(self) -> None:
        action = DeleteTag(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='_',
                value='_',
                index=100,
            ),
            index=100,
        )
        assert not action.validate(self.indexed_actions)

    def test_execute(self) -> None:
        tag_id = 'tag_3'
        tag_item = TagItem(
            id=tag_id,
            value='_',
        )
        action = DeleteTag(
            taxonomy=self.taxonomy,
            tag=tag_item,
            index=100,
        )
        assert self.taxonomy.tag_set.filter(external_id=tag_id).exists()
        action.execute()
        assert not self.taxonomy.tag_set.filter(external_id=tag_id).exists()


class TestWithoutChanges(TestImportActionMixin, TestCase):
    """
    Test for 'without_changes' action
    """
    def test_applies_for(self) -> None:
        result = WithoutChanges.applies_for(
            self.taxonomy,
            tag=TagItem(
                id='_',
                value='_',
                index=100,
            ),
        )
        self.assertFalse(result)

    def test_validate(self) -> None:
        action = WithoutChanges(
            taxonomy=self.taxonomy,
            tag=TagItem(
                id='_',
                value='_',
                index=100,
            ),
            index=100,
        )
        result = action.validate(self.indexed_actions)
        self.assertEqual(result, [])
