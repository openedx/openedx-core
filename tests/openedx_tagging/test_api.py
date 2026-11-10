"""
Test the tagging APIs
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

import openedx_tagging.api as tagging_api
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

from .test_models import TestTagTaxonomyMixin, get_tag
from .utils import pretty_format_tags

tag_values_for_autocomplete_test = [
    'Archaea',
    'Archaebacteria',
    'Animalia',
    'Arthropoda',
    'Plantae',
    'Monera',
    'Gastrotrich',
    'Placozoa',
]


@ddt.ddt
class TestApiTagging(TestTagTaxonomyMixin, TestCase):
    """
    Test the Tagging API methods.
    """

    def test_create_taxonomy(self) -> None:  # Note: we must specify '-> None' to opt in to type checking
        params: dict[str, Any] = {
            "name": "Difficulty",
            "description": "This taxonomy contains tags describing the difficulty of an activity",
            "enabled": False,
            "allow_multiple": True,
            "allow_free_text": True,
            "export_id": "difficulty",
        }
        taxonomy = tagging_api.create_taxonomy(**params)
        for param, value in params.items():
            assert getattr(taxonomy, param) == value
        assert not taxonomy.read_only
        assert taxonomy.visible_to_authors

    def test_create_taxonomy_read_only(self) -> None:
        taxonomy = tagging_api.create_taxonomy(name="Read Only", read_only=True)
        assert taxonomy.read_only

    def test_create_taxonomy_without_export_id(self) -> None:
        params: dict[str, Any] = {
            "name": "Taxonomy Data: test 3",
            "enabled": False,
            "allow_multiple": True,
            "allow_free_text": True,
        }
        taxonomy = tagging_api.create_taxonomy(**params)
        assert taxonomy.export_id == "5-taxonomy-data-test-3"

    def test_get_taxonomy(self) -> None:
        tax1 = tagging_api.get_taxonomy(1)
        assert tax1 == self.taxonomy
        # Not a small id like 200: MySQL doesn't reset auto-increment between tests, so taxonomies
        # created earlier (e.g. self.free_text_taxonomy in TestTagTaxonomyMixin.setUp()) can take it.
        no_tax = tagging_api.get_taxonomy(2**31 - 1)
        assert no_tax is None

    def test_get_taxonomy_by_export_id(self) -> None:
        tax1 = tagging_api.get_taxonomy_by_export_id("life_on_earth")
        assert tax1 == self.taxonomy

        no_tax = tagging_api.get_taxonomy_by_export_id("nope")
        assert no_tax is None

    def test_get_taxonomies(self) -> None:
        tax1 = tagging_api.create_taxonomy("Enabled")
        tax2 = tagging_api.create_taxonomy("Disabled", enabled=False)
        tax3 = Taxonomy.objects.get(name="Import Taxonomy Test")
        with self.assertNumQueries(1):
            enabled = list(tagging_api.get_taxonomies())
        assert enabled == [
            tax1,
            self.free_text_taxonomy,
            tax3,
            self.taxonomy,
            self.read_only_taxonomy,
        ]
        assert str(enabled[0]) == f"<Taxonomy> ({tax1.id}) Enabled"
        assert str(enabled[1]) == f"<Taxonomy> ({self.free_text_taxonomy.id}) Free Text"
        assert str(enabled[2]) == "<Taxonomy> (5) Import Taxonomy Test"
        assert str(enabled[3]) == "<Taxonomy> (1) Life on Earth"
        assert str(enabled[4]) == "<Taxonomy> (4) Read Only Taxonomy"

        with self.assertNumQueries(1):
            disabled = list(tagging_api.get_taxonomies(enabled=False))
        assert disabled == [tax2]
        assert str(disabled[0]) == f"<Taxonomy> ({tax2.id}) Disabled"

        with self.assertNumQueries(1):
            both = list(tagging_api.get_taxonomies(enabled=None))
        assert both == [
            tax2,
            tax1,
            self.free_text_taxonomy,
            tax3,
            self.taxonomy,
            self.read_only_taxonomy,
        ]

    def test_get_tags(self) -> None:
        assert pretty_format_tags(tagging_api.get_tags(self.taxonomy), parent=False) == [
            "Archaea (children: 3)",
            "  DPANN (children: 0)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            "Bacteria (children: 2)",
            "  Archaebacteria (children: 0)",
            "  Eubacteria (children: 0)",
            "Eukaryota (children: 5)",
            "  Animalia (children: 7)",
            "    Arthropoda (children: 0)",
            "    Chordata (children: 1)",
            "      Mammalia (children: 0)",
            "    Cnidaria (children: 0)",
            "    Ctenophora (children: 0)",
            "    Gastrotrich (children: 0)",
            "    Placozoa (children: 0)",
            "    Porifera (children: 0)",
            "  Fungi (children: 0)",
            "  Monera (children: 0)",
            "  Plantae (children: 0)",
            "  Protista (children: 0)",
        ]

    def test_get_tags_system(self) -> None:
        assert pretty_format_tags(tagging_api.get_tags(self.read_only_taxonomy), parent=False) == [
            "System Tag 1 (children: 0)",
            "System Tag 2 (children: 0)",
            "System Tag 3 (children: 0)",
            "System Tag 4 (children: 0)",
        ]

    def test_get_root_tags(self):
        root_life_on_earth_tags = tagging_api.get_root_tags(self.taxonomy)
        assert pretty_format_tags(root_life_on_earth_tags, parent=False) == [
            'Archaea (children: 3)',
            'Bacteria (children: 2)',
            'Eukaryota (children: 5)',
        ]

    def test_get_root_tags_system(self):
        result = tagging_api.get_root_tags(self.read_only_taxonomy)
        assert pretty_format_tags(result, parent=False) == [
            'System Tag 1 (children: 0)',
            'System Tag 2 (children: 0)',
            'System Tag 3 (children: 0)',
            'System Tag 4 (children: 0)',
        ]

    def test_search_tags(self) -> None:
        result = tagging_api.search_tags(self.taxonomy, search_term='eU')
        assert pretty_format_tags(result, parent=False) == [
            'Archaea (children: 1)',  # Doesn't match 'eU' but is included because a child is included
            '  Euryarchaeida (children: 0)',
            'Bacteria (children: 1)',  # Doesn't match 'eU' but is included because a child is included
            '  Eubacteria (children: 0)',
            'Eukaryota (children: 0)',
        ]

    def test_get_children_tags(self) -> None:
        """
        Test getting the children of a particular tag in a closed taxonomy.
        """
        result1 = tagging_api.get_children_tags(self.taxonomy, "Animalia")
        assert pretty_format_tags(result1, parent=False) == [
            '    Arthropoda (children: 0)',
            '    Chordata (children: 1)',
            # Mammalia is a child of Chordata but excluded here.
            '    Cnidaria (children: 0)',
            '    Ctenophora (children: 0)',
            '    Gastrotrich (children: 0)',
            '    Placozoa (children: 0)',
            '    Porifera (children: 0)',
        ]

    def test_get_children_tags_invalid_taxonomy(self) -> None:
        """
        Calling get_children_tags on free text taxonomies gives an error.
        """
        free_text_taxonomy = tagging_api.create_taxonomy(
            name="FreeText",
            allow_free_text=True,
        )
        tagging_api.tag_object(object_id="obj1", taxonomy=free_text_taxonomy, tags=["some_tag"])
        with self.assertRaises(ValueError) as exc:
            tagging_api.get_children_tags(free_text_taxonomy, "some_tag")
        assert "Cannot specify a parent tag ID for free text taxonomies" in str(exc.exception)

    def test_get_children_tags_no_children(self) -> None:
        """
        Trying to get children of a system tag that has no children yields an empty result:
        """
        assert not list(tagging_api.get_children_tags(self.read_only_taxonomy, self.read_only_taxonomy_tag.value))

    def test_get_ancestor_tags(self) -> None:
        """
        get_ancestor_tags() returns every ancestor of a multi-level tag, up to the root.
        """
        eukaryota = get_tag("Eukaryota")
        result = {tag.value for tag in tagging_api.get_ancestor_tags(self.mammalia)}
        assert result == {eukaryota.value, self.animalia.value, self.chordata.value}

    def test_get_ancestor_tags_root_has_no_ancestors(self) -> None:
        """
        A root tag (no parent) has no ancestors.
        """
        assert not list(tagging_api.get_ancestor_tags(self.archaea))

    def test_get_ancestor_tags_free_text_taxonomy(self) -> None:
        """
        A tag on a free-text-enabled taxonomy has no taxonomy-defined hierarchy to walk.
        """
        tag = Tag.objects.create(taxonomy=self.free_text_taxonomy, value="some_tag")
        assert not list(tagging_api.get_ancestor_tags(tag))

    def test_get_descendant_tags(self) -> None:
        """
        get_descendant_tags() returns every descendant at any depth, including grandchildren.
        """
        result = {tag.value for tag in tagging_api.get_descendant_tags(self.animalia)}
        assert result == {
            "Arthropoda",
            self.chordata.value,
            self.mammalia.value,  # A grandchild of Animalia (via Chordata), not just a direct child.
            "Cnidaria",
            "Ctenophora",
            "Gastrotrich",
            "Placozoa",
            "Porifera",
        }

    def test_get_descendant_tags_leaf_has_no_descendants(self) -> None:
        """
        A leaf tag (no children) has no descendants.
        """
        assert not list(tagging_api.get_descendant_tags(self.mammalia))

    def test_get_descendant_tags_free_text_taxonomy(self) -> None:
        """
        A tag on a free-text-enabled taxonomy has no taxonomy-defined hierarchy to walk.
        """
        tag = Tag.objects.create(taxonomy=self.free_text_taxonomy, value="some_tag")
        assert not list(tagging_api.get_descendant_tags(tag))

    def test_resync_object_tags(self) -> None:
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        open_taxonomy = tagging_api.create_taxonomy(
            name="Freetext Life",
            allow_free_text=True,
            allow_multiple=True,
        )

        object_id = "obj1"
        # Create some tags:
        tagging_api.tag_object(object_id, self.taxonomy, [self.archaea.value, self.bacteria.value])  # Regular tags
        tagging_api.tag_object(object_id, open_taxonomy, ["foo", "bar"])  # Free text tags

        # At first, none of these will be deleted:
        assert [(t.value, t.is_deleted) for t in tagging_api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", False),
            ("foo", False),
            (self.archaea.value, False),
            (self.bacteria.value, False),
        ]

        # Delete "bacteria" from the taxonomy:
        tagging_api.delete_tags_from_taxonomy(self.taxonomy, [self.bacteria.value], with_subtags=True)

        assert [(t.value, t.is_deleted) for t in tagging_api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", False),
            ("foo", False),
            (self.archaea.value, False),
            (self.bacteria.value, True),  # <--- deleted! But the value is preserved.
        ]

        # Re-syncing the tags at this point does nothing:
        tagging_api.resync_object_tags()

        # Now re-create the tag
        self.bacteria.save()

        # Then re-sync the tags:
        changed = tagging_api.resync_object_tags()
        assert changed == 1

        # Now the tag is not deleted:
        assert [(t.value, t.is_deleted) for t in tagging_api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", False),
            ("foo", False),
            (self.archaea.value, False),
            (self.bacteria.value, False),  # <--- not deleted
        ]

        # Re-syncing the tags now does nothing:
        changed = tagging_api.resync_object_tags()
        assert changed == 0

    def test_resync_object_tags_without_tag(self) -> None:
        # Create object tag with an invalid tag
        tag_value = "Eukaryota Xenomorph"
        tagging_api.tag_object(
            object_id="biology101",
            taxonomy=self.taxonomy,
            tags=[tag_value],
            create_invalid=True,
        )

        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 0

        # Create tag
        tag = Tag(
            taxonomy=self.taxonomy,
            value=tag_value,
        )
        tag.save()

        # Resync object tags
        changed = tagging_api.resync_object_tags()
        assert changed == 1

        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1
        object_tag = object_tags[0]
        assert object_tag.taxonomy == self.taxonomy
        assert object_tag.tag == tag

    def test_resync_object_tags_without_taxonomy(self) -> None:
        # Create object tag with an invalid taxonomy
        tag_value = "Eukaryota Xenomorph"
        tagging_api.tag_object(
            object_id="biology101",
            taxonomy=None,
            tags=[tag_value],
            create_invalid=True,
            taxonomy_export_id='taxonomy_test'
        )

        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 0

        # Create taxonomy
        new_taxonomy = tagging_api.create_taxonomy(
            'Taxonomy_test',
            export_id='taxonomy_test'
        )
        new_taxonomy.save()
        # Create tag
        tag = Tag(
            taxonomy=new_taxonomy,
            value=tag_value,
        )
        tag.save()

        # Resync object tags
        changed = tagging_api.resync_object_tags()
        assert changed == 1

        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1
        object_tag = object_tags[0]
        assert object_tag.taxonomy == new_taxonomy
        assert object_tag.tag == tag

    def test_tag_object(self):
        self.taxonomy.allow_multiple = True

        test_tags = [
            [
                self.archaea,
                self.eubacteria,
                self.chordata,
            ],
            [
                self.archaebacteria,
                self.chordata,
            ],
            [
                self.archaea,
                self.archaebacteria,
            ],
        ]

        # Tag and re-tag the object, checking that the expected tags are returned and deleted
        for tag_list in test_tags:
            tagging_api.tag_object(
                "biology101",
                self.taxonomy,
                [t.value for t in tag_list],
            )
            # Ensure the expected number of tags exist in the database
            object_tags = tagging_api.get_object_tags("biology101", taxonomy_id=self.taxonomy.id)
            # And the expected number of tags were returned
            assert len(object_tags) == len(tag_list)
            for index, object_tag in enumerate(object_tags):
                object_tag.full_clean()  # Should not raise any ValidationErrors
                assert object_tag.tag_id == tag_list[index].id
                assert object_tag._value == tag_list[index].value  # pylint: disable=protected-access
                assert object_tag.taxonomy == self.taxonomy
                assert object_tag.export_id == self.taxonomy.export_id
                assert object_tag.object_id == "biology101"

    def test_tag_object_free_text(self) -> None:
        """
        Test tagging an object using a free text taxonomy
        """
        tagging_api.tag_object(
            object_id="biology101",
            taxonomy=self.free_text_taxonomy,
            tags=["Eukaryota Xenomorph"],
        )
        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1
        object_tag = object_tags[0]
        object_tag.full_clean()  # Should not raise any ValidationErrors
        assert object_tag.taxonomy == self.free_text_taxonomy
        assert object_tag.export_id == self.free_text_taxonomy.export_id
        assert object_tag._value == "Eukaryota Xenomorph"  # pylint: disable=protected-access
        assert object_tag.get_lineage() == ["Eukaryota Xenomorph"]
        assert object_tag.object_id == "biology101"

    def test_tag_object_no_multiple(self):
        with pytest.raises(ValueError) as excinfo:
            tagging_api.tag_object("biology101", self.taxonomy, ["A", "B"])
        assert "only allows one tag per object" in str(excinfo.value)

    def test_tag_object_invalid_tag(self):
        with pytest.raises(tagging_api.TagDoesNotExist) as excinfo:
            tagging_api.tag_object("biology101", self.taxonomy, ["Eukaryota Xenomorph"])
        assert "Tag matching query does not exist." in str(excinfo.value)

    def test_tag_object_create_without_tag(self):
        tagging_api.tag_object(
            object_id="biology101",
            taxonomy=self.taxonomy,
            tags=["Eukaryota Xenomorph"],
            create_invalid=True,
        )
        object_tags = tagging_api.get_object_tags(
            "biology101",
            include_deleted=True,
        )
        assert len(object_tags) == 1
        object_tag = object_tags[0]
        object_tag.full_clean()  # Should not raise any ValidationErrors
        assert object_tag.taxonomy == self.taxonomy
        assert object_tag.export_id == self.taxonomy.export_id
        assert object_tag.tag is None
        assert object_tag._value == "Eukaryota Xenomorph"  # pylint: disable=protected-access
        assert object_tag.get_lineage() == ["Eukaryota Xenomorph"]
        assert object_tag.object_id == "biology101"

    def test_tag_object_create_without_taxonomy(self):
        tagging_api.tag_object(
            object_id="biology101",
            taxonomy=None,
            tags=["Eukaryota Xenomorph"],
            create_invalid=True,
            taxonomy_export_id='test_taxonomy'
        )
        object_tags = tagging_api.get_object_tags(
            "biology101",
            include_deleted=True,
        )
        assert len(object_tags) == 1
        object_tag = object_tags[0]
        object_tag.full_clean()  # Should not raise any ValidationErrors
        assert object_tag.taxonomy is None
        assert object_tag.export_id == 'test_taxonomy'
        assert object_tag.tag is None
        assert object_tag._value == "Eukaryota Xenomorph"  # pylint: disable=protected-access
        assert object_tag.get_lineage() == ["Eukaryota Xenomorph"]
        assert object_tag.object_id == "biology101"

    def test_tag_object_taxonomy_export_error(self):
        with self.assertRaises(ValueError) as exc:
            tagging_api.tag_object(
                object_id="biology101",
                taxonomy=None,
                tags=["Eukaryota Xenomorph"],
                create_invalid=True,
                taxonomy_export_id=None
            )
            assert exc.exception
            assert "`taxonomy_export_id` can't be None if `taxonomy` is None" in str(exc.exception)

    def test_tag_object_string(self) -> None:
        with self.assertRaises(ValueError) as exc:
            tagging_api.tag_object(
                "biology101",
                self.taxonomy,
                'string',  # type: ignore[arg-type]
            )
        assert "Tags must be a list, not str." in str(exc.exception)

    def test_tag_object_integer(self) -> None:
        with self.assertRaises(ValueError) as exc:
            tagging_api.tag_object(
                "biology101",
                self.taxonomy,
                1,  # type: ignore[arg-type]
            )
        assert "Tags must be a list, not int." in str(exc.exception)

    def test_tag_object_same_id(self) -> None:
        # Tag the object with the same tag twice
        tagging_api.tag_object(
            "biology101",
            self.taxonomy,
            [self.eubacteria.value],
        )
        tagging_api.tag_object(
            "biology101",
            self.taxonomy,
            [self.eubacteria.value],
        )
        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1
        assert str(object_tags[0]) == "<ObjectTag> biology101: Life on Earth=Eubacteria"

    def test_tag_object_same_value(self) -> None:
        # Tag the object with the same value twice
        tagging_api.tag_object(
            "biology101",
            self.taxonomy,
            [self.eubacteria.value, self.eubacteria.value],
        )
        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1
        assert str(object_tags[0]) == "<ObjectTag> biology101: Life on Earth=Eubacteria"

    def test_tag_object_same_value_multiple_free(self) -> None:
        self.taxonomy.allow_multiple = True
        self.taxonomy.allow_free_text = True
        self.taxonomy.save()
        # Tag the object with the same value twice
        tagging_api.tag_object(
            "biology101",
            self.taxonomy,
            ["tag1", "tag1"],
        )
        object_tags = tagging_api.get_object_tags("biology101")
        assert len(object_tags) == 1

    def test_tag_object_case_id(self) -> None:
        """
        Test that the case of the object_id is preserved.
        """
        tagging_api.tag_object(
            "biology101",
            self.taxonomy,
            [self.eubacteria.value],
        )

        tagging_api.tag_object(
            "BIOLOGY101",
            self.taxonomy,
            [self.archaea.value],
        )

        object_tags_lower = tagging_api.get_object_tags(
            taxonomy_id=self.taxonomy.pk,
            object_id="biology101",
        )

        assert len(object_tags_lower) == 1
        assert object_tags_lower[0].tag_id == self.eubacteria.id

        object_tags_upper = tagging_api.get_object_tags(
            taxonomy_id=self.taxonomy.pk,
            object_id="BIOLOGY101",
        )

        assert len(object_tags_upper) == 1
        assert object_tags_upper[0].tag_id == self.archaea.id

    def test_tag_object_read_only_taxonomy(self) -> None:
        tags_list = [
            ["System Tag 1"],
            ["System Tag 2"],
        ]

        for tags in tags_list:
            tagging_api.tag_object("biology101", self.read_only_taxonomy, tags)

            # Ensure the expected number of tags exist in the database
            object_tags = tagging_api.get_object_tags("biology101")
            # And the expected number of tags were returned
            assert len(object_tags) == len(tags)
            for index, object_tag in enumerate(object_tags):
                object_tag.full_clean()  # Check full model validation
                assert object_tag.value == tags[index]
                assert not object_tag.is_deleted
                assert object_tag.taxonomy == self.read_only_taxonomy
                assert object_tag.export_id == self.read_only_taxonomy.export_id
                assert object_tag.object_id == "biology101"

    def test_tag_object_read_only_taxonomy_invalid(self) -> None:
        with self.assertRaises(tagging_api.TagDoesNotExist):
            tagging_api.tag_object(
                "biology101",
                self.read_only_taxonomy,
                ["Not a real tag"],
            )

    def test_tag_object_limit(self) -> None:
        """
        Test that the tagging limit is enforced.
        """
        dummy_taxonomies = self.create_100_taxonomies()
        # The user can add up to 100 tags to a object
        for taxonomy in dummy_taxonomies:
            tagging_api.tag_object(
                "object_1",
                taxonomy,
                ["Dummy Tag"],
            )

        # Adding a new tag should fail
        with self.assertRaises(ValueError) as exc:
            tagging_api.tag_object(
                "object_1",
                self.taxonomy,
                ["Eubacteria"],
            )
        assert exc.exception
        assert "Cannot add more than 100 tags to" in str(exc.exception)

        # Updating existing tags should work
        for taxonomy in dummy_taxonomies:
            tagging_api.tag_object(
                "object_1",
                taxonomy,
                ["New Dummy Tag"],
            )

        # Updating existing tags adding a new one should fail
        for taxonomy in dummy_taxonomies:
            with self.assertRaises(ValueError) as exc:
                tagging_api.tag_object(
                    "object_1",
                    taxonomy,
                    ["New Dummy Tag 1", "New Dummy Tag 2"],
                )
            assert exc.exception
            assert "Cannot add more than 100 tags to" in str(exc.exception)

    def test_get_object_tags_deleted_disabled(self) -> None:
        """
        Test that get_object_tags doesn't return tags from disabled taxonomies
        or tags that have been deleted or taxonomies that have been deleted.
        """
        obj_id = "object_id1"
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        disabled_taxonomy = tagging_api.create_taxonomy("Disabled Taxonomy", allow_free_text=True)
        tagging_api.tag_object(object_id=obj_id, taxonomy=self.taxonomy, tags=["DPANN", "Chordata"])
        tagging_api.tag_object(object_id=obj_id, taxonomy=self.read_only_taxonomy, tags=["System Tag 1"])
        tagging_api.tag_object(object_id=obj_id, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])
        tagging_api.tag_object(object_id=obj_id, taxonomy=disabled_taxonomy, tags=["disabled tag"])

        def get_object_tags():
            return [f"{ot.export_id}: {'>'.join(ot.get_lineage())}" for ot in tagging_api.get_object_tags(obj_id)]

        # Before deleting/disabling:
        assert get_object_tags() == [
            "5-disabled-taxonomy: disabled tag",
            "4-free-text: has a notochord",
            "life_on_earth: Archaea>DPANN",
            "life_on_earth: Eukaryota>Animalia>Chordata",
            "read_only_taxonomy: System Tag 1",
        ]

        # Now delete and disable things:
        disabled_taxonomy.enabled = False
        disabled_taxonomy.save()
        with patch("openedx_tagging.signal_handlers._is_explicit_tag_delete", return_value=False):
            self.free_text_taxonomy.delete()
        tagging_api.delete_tags_from_taxonomy(self.taxonomy, ["DPANN"], with_subtags=False)

        # Now retrieve the tags again:
        assert get_object_tags() == [
            "life_on_earth: Eukaryota>Animalia>Chordata",
            "read_only_taxonomy: System Tag 1",
        ]

    @ddt.data(
        ("ChA", [
            "Archaea (children: 2)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            "Bacteria (children: 1)",  # does not contain "cha" but a child does
            "  Archaebacteria (children: 0)",
        ]),
        ("ar", [
            "Archaea (children: 2)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            "Bacteria (children: 1)",  # does not contain "ar" but a child does
            "  Archaebacteria (children: 0)",
            "Eukaryota (children: 1)",
            "  Animalia (children: 2)",  # does not contain "ar" but a child does
            "    Arthropoda (children: 0)",
            "    Cnidaria (children: 0)",
        ]),
        ("aE", [
            "Archaea (children: 2)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            "Bacteria (children: 1)",  # does not contain "ae" but a child does
            "  Archaebacteria (children: 0)",
            "Eukaryota (children: 1)",  # does not contain "ae" but a child does
            "  Plantae (children: 0)",
        ]),
        ("a", [
            "Archaea (children: 3)",
            "  DPANN (children: 0)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            "Bacteria (children: 2)",
            "  Archaebacteria (children: 0)",
            "  Eubacteria (children: 0)",
            "Eukaryota (children: 4)",
            "  Animalia (children: 7)",
            "    Arthropoda (children: 0)",
            "    Chordata (children: 1)",
            "      Mammalia (children: 0)",
            "    Cnidaria (children: 0)",
            "    Ctenophora (children: 0)",
            "    Gastrotrich (children: 0)",
            "    Placozoa (children: 0)",
            "    Porifera (children: 0)",
            "  Monera (children: 0)",
            "  Plantae (children: 0)",
            "  Protista (children: 0)",
        ]),
    )
    @ddt.unpack
    def test_autocomplete_tags_closed(self, search: str, expected: list[str]) -> None:
        """
        Test autocompletion/search for tags using a closed taxonomy.
        """
        closed_taxonomy = self.taxonomy
        for index, value in enumerate(tag_values_for_autocomplete_test):
            # Creating ObjectTags for closed taxonomy
            tag = get_tag(value)
            ObjectTag(
                object_id=f"object_id_{index}",
                taxonomy=closed_taxonomy,
                tag=tag,
                _value=value,
            ).save()

        result = tagging_api.search_tags(closed_taxonomy, search)
        assert pretty_format_tags(result, parent=False) == expected

    def test_autocomplete_tags_closed_omit_object(self) -> None:
        """
        Test autocomplete search that omits the tags from a specific object
        """
        object_id = 'new_object_id'
        tagging_api.tag_object(object_id=object_id, taxonomy=self.taxonomy, tags=["Archaebacteria"])
        result = tagging_api.search_tags(self.taxonomy, "ChA", exclude_object_id=object_id)
        assert pretty_format_tags(result, parent=False) == [
            "Archaea (children: 2)",
            "  Euryarchaeida (children: 0)",
            "  Proteoarchaeota (children: 0)",
            # These results are no longer included because of exclude_object_id:
            # "Bacteria (children: 1)",  # does not contain "cha" but a child does
            # "  Archaebacteria (children: 0)",
        ]

    def test_get_object_tag_counts(self) -> None:
        """
        Basic test of get_object_tag_counts
        """
        obj1 = "object_id1"
        obj2 = "object_id2"
        other = "other_object"
        # Give each object 1-2 tags:
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.taxonomy, tags=["Chordata"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])
        tagging_api.tag_object(object_id=other, taxonomy=self.free_text_taxonomy, tags=["other"])

        assert tagging_api.get_object_tag_counts(obj1) == {obj1: 1}
        assert tagging_api.get_object_tag_counts(obj2) == {obj2: 2}
        assert tagging_api.get_object_tag_counts(f"{obj1},{obj2}") == {obj1: 1, obj2: 2}
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 1, obj2: 2}

    def test_get_object_tag_counts_implicit(self) -> None:
        """
        Basic test of get_object_tag_counts, including implicit (parent) tags

        Note that:
            - "DPANN" is "Archaea > DPANN" (2 tags, 1 implicit), and
            - "Chordata" is "Eukaryota > Animalia > Chordata" (3 tags, 2 implicit)
            - "Arthropoda" is "Eukaryota > Animalia > Arthropoda" (same)
        """
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        obj1, obj2, obj3 = "object_id1", "object_id2", "object_id3"
        other = "other_object"
        # Give each object 1-2 tags:
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.taxonomy, tags=["Chordata"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])
        tagging_api.tag_object(object_id=obj3, taxonomy=self.taxonomy, tags=["Chordata", "Arthropoda"])
        tagging_api.tag_object(object_id=other, taxonomy=self.free_text_taxonomy, tags=["other"])

        assert tagging_api.get_object_tag_counts(obj1, count_implicit=True) == {obj1: 2}
        assert tagging_api.get_object_tag_counts(obj2, count_implicit=True) == {obj2: 4}
        assert tagging_api.get_object_tag_counts(f"{obj1},{obj2}", count_implicit=True) == {obj1: 2, obj2: 4}
        assert tagging_api.get_object_tag_counts("object_*", count_implicit=True) == {
            obj1: 2,
            obj2: 4,
            obj3: 4,  # obj3 has 2 explicit tags and 2 implicit tags (not 4 because the implicit tags are the same)
        }
        assert tagging_api.get_object_tag_counts(other, count_implicit=True) == {other: 1}

    def test_get_object_tag_counts_deleted_disabled(self) -> None:
        """
        Test that get_object_tag_counts doesn't "count" disabled taxonomies or
        deleted tags.
        """
        obj1 = "object_id1"
        obj2 = "object_id2"
        # Give each object 2 tags:
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id=obj1, taxonomy=self.read_only_taxonomy, tags=["System Tag 1"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.taxonomy, tags=["Chordata"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])

        # Before we delete tags / disable taxonomies, the counts are two each:
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 2, obj2: 2}
        # Delete the "DPANN" tag from self.taxonomy:
        tagging_api.delete_tags_from_taxonomy(self.taxonomy, tags=["DPANN"], with_subtags=False)
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 1, obj2: 2}
        # Disable the free text taxonomy:
        self.free_text_taxonomy.enabled = False
        self.free_text_taxonomy.save()
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 1, obj2: 1}
        # Also check the result with count_implicit:
        # "System Tag 1" has no implicit tags but "Chordata" has two, so we expect these totals:
        assert tagging_api.get_object_tag_counts("object_*", count_implicit=True) == {obj1: 1, obj2: 3}

        # But, by the way, if we re-enable the taxonomy and restore the tag, the counts return:
        self.free_text_taxonomy.enabled = True
        self.free_text_taxonomy.save()
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 1, obj2: 2}
        tagging_api.add_tag_to_taxonomy(self.taxonomy, "DPANN", parent_tag_value="Archaea")
        assert tagging_api.get_object_tag_counts("object_*") == {obj1: 2, obj2: 2}

    def test_copy_tags(self) -> None:
        obj1 = "object_id1"
        obj2 = "object_id2"

        tags_list = [
            {
                "value": "DPANN",
                "taxonomy": self.taxonomy,
            },
            {
                "value": "System Tag 1",
                "taxonomy": self.read_only_taxonomy,
            },
        ]

        for tag_object in tags_list:
            tagging_api.tag_object(object_id=obj1, taxonomy=tag_object["taxonomy"], tags=[tag_object["value"]])

        tagging_api.copy_tags(obj1, obj2)

        object_tags = tagging_api.get_object_tags(obj2)

        assert len(object_tags) == 2
        for index, object_tag in enumerate(object_tags):
            object_tag.full_clean()
            assert object_tag.value == tags_list[index]["value"]
            assert not object_tag.is_deleted
            assert object_tag.taxonomy == tags_list[index]["taxonomy"]
            assert object_tag.object_id == obj2
            assert object_tag.is_copied is True

    def test_copy_tags_with_non_copied(self) -> None:
        obj1 = "object_id1"
        obj2 = "object_id2"

        tagging_api.tag_object(object_id=obj1, taxonomy=self.read_only_taxonomy, tags=["System Tag 1"])

        tagging_api.tag_object(object_id=obj2, taxonomy=self.taxonomy, tags=["Chordata"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])

        tagging_api.copy_tags(obj1, obj2)
        object_tags = tagging_api.get_object_tags(obj2)

        # Tags must be the non-copied and the copied tag.
        expected_tags = [
            {
                "value": "has a notochord",
                "taxonomy": self.free_text_taxonomy,
                "copied": False,
            },
            {
                "value": "Chordata",
                "taxonomy": self.taxonomy,
                "copied": False,
            },
            {
                "value": "System Tag 1",
                "taxonomy": self.read_only_taxonomy,
                "copied": True,
            },
        ]
        assert len(object_tags) == 3
        for index, object_tag in enumerate(object_tags):
            assert object_tag.value == expected_tags[index]["value"]
            assert not object_tag.is_deleted
            assert object_tag.taxonomy == expected_tags[index]["taxonomy"]
            assert object_tag.object_id == obj2
            assert object_tag.is_copied == expected_tags[index]["copied"]

        # Delete tags of 'obj1' and add other
        tagging_api.delete_object_tags(obj1)
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.copy_tags(obj1, obj2)
        object_tags = tagging_api.get_object_tags(obj2)

        # Tags must be the non-copied and the new copied tag.
        # The previous copied tags must be deleted.
        expected_tags = [
            {
                "value": "has a notochord",
                "taxonomy": self.free_text_taxonomy,
                "copied": False,
            },
            {
                "value": "DPANN",
                "taxonomy": self.taxonomy,
                "copied": True,
            },
            {
                "value": "Chordata",
                "taxonomy": self.taxonomy,
                "copied": False,
            },
        ]
        assert len(object_tags) == 3
        for index, object_tag in enumerate(object_tags):
            assert object_tag.value == expected_tags[index]["value"]
            assert not object_tag.is_deleted
            assert object_tag.taxonomy == expected_tags[index]["taxonomy"]
            assert object_tag.object_id == obj2
            assert object_tag.is_copied == expected_tags[index]["copied"]

        # Add a tag used by 'obj2'
        tagging_api.tag_object(object_id=obj1, taxonomy=self.free_text_taxonomy, tags=["has a notochord"])
        tagging_api.copy_tags(obj1, obj2)
        object_tags = tagging_api.get_object_tags(obj2)

        # The non-copied tag 'has a notochord' must be copied now.
        expected_tags = [
            {
                "value": "has a notochord",
                "taxonomy": self.free_text_taxonomy,
                "copied": True,
            },
            {
                "value": "DPANN",
                "taxonomy": self.taxonomy,
                "copied": True,
            },
            {
                "value": "Chordata",
                "taxonomy": self.taxonomy,
                "copied": False,
            },
        ]
        assert len(object_tags) == 3
        for index, object_tag in enumerate(object_tags):
            assert object_tag.value == expected_tags[index]["value"]
            assert not object_tag.is_deleted
            assert object_tag.taxonomy == expected_tags[index]["taxonomy"]
            assert object_tag.object_id == obj2
            assert object_tag.is_copied == expected_tags[index]["copied"]

    def test_unmark_copied_tags(self) -> None:
        obj1 = "object_id1"
        obj2 = "object_id2"

        # Put 2 tags on obj1
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id=obj1, taxonomy=self.read_only_taxonomy, tags=["System Tag 1"])

        # Copy tags from obj1 to obj2
        tagging_api.copy_tags(obj1, obj2)

        # Update obj2's tags to include one non-copied tag
        tagging_api.tag_object(object_id=obj2, taxonomy=self.read_only_taxonomy, tags=["System Tag 1", "System Tag 2"])

        tags = tagging_api.get_object_tags(obj2)
        assert tags.filter(is_copied=False).count() == 1
        assert tags.filter(is_copied=True).count() == 2

        tagging_api.unmark_copied_tags(obj2)

        tags = tagging_api.get_object_tags(obj2)
        assert tags.filter(is_copied=False).count() == 3
        assert tags.filter(is_copied=True).count() == 0

    def test_deep_taxonomy(self) -> None:
        """
        Test that a taxonomy's tags can be nested deeply
        """
        taxonomy = tagging_api.create_taxonomy(name="Deep Taxonomy")

        tag_0 = tagging_api.add_tag_to_taxonomy(taxonomy, "Root - depth 0")
        assert tag_0.depth == 0

        tag_1 = tagging_api.add_tag_to_taxonomy(taxonomy, "Child - depth 1", parent_tag_value=tag_0.value)
        assert tag_1.depth == 1

        tag_2 = tagging_api.add_tag_to_taxonomy(taxonomy, "Grandchild - depth 2", parent_tag_value=tag_1.value)
        assert tag_2.depth == 2

        # Up to this point is the level at which all the APIs are guaranteed to work correctly.

        tag_3 = tagging_api.add_tag_to_taxonomy(taxonomy, "Great-Grandchild - depth 3", parent_tag_value=tag_2.value)
        assert tag_3.depth == 3

        tag_4 = tagging_api.add_tag_to_taxonomy(
            taxonomy, "Great-Great-Grandchild - depth 4", parent_tag_value=tag_3.value
        )
        assert tag_4.depth == 4

        tag_5 = tagging_api.add_tag_to_taxonomy(
            taxonomy, "Great-Great-Great-Grandchild - depth 5", parent_tag_value=tag_4.value
        )
        assert tag_5.depth == 5

        assert pretty_format_tags(
            tagging_api.get_tags(taxonomy)
        ) == [
            "Root - depth 0 (None) (children: 1)",
            "  Child - depth 1 (Root - depth 0) (children: 1)",
            "    Grandchild - depth 2 (Child - depth 1) (children: 1)",
            "      Great-Grandchild - depth 3 (Grandchild - depth 2) (children: 1)",
            "        Great-Great-Grandchild - depth 4 (Great-Grandchild - depth 3) (children: 1)",
            "          Great-Great-Great-Grandchild - depth 5 (Great-Great-Grandchild - depth 4) (children: 0)",
        ]
        # And we can load deep levels one level at a time:
        assert pretty_format_tags(
            tagging_api.get_children_tags(taxonomy, parent_tag_value=tag_3.value)
        ) == [
            '        Great-Great-Grandchild - depth 4 (Great-Grandchild - depth 3) (children: 1)',
        ]
        assert pretty_format_tags(
            tagging_api.get_children_tags(taxonomy, parent_tag_value=tag_4.value)
        ) == [
            '          Great-Great-Great-Grandchild - depth 5 (Great-Great-Grandchild - depth 4) (children: 0)',
        ]
        # Or even load a subtree:
        deep_result = taxonomy.get_filtered_tags(depth=None, parent_tag_value=tag_2.value)
        assert pretty_format_tags(deep_result, parent=False) == [
            '      Great-Grandchild - depth 3 (children: 1)',
            '        Great-Great-Grandchild - depth 4 (children: 1)',
            '          Great-Great-Great-Grandchild - depth 5 (children: 0)',
        ]
        # Let's check that all the fields on the last entry, especially 'depth':
        assert deep_result[2]["value"] == "Great-Great-Great-Grandchild - depth 5"
        assert deep_result[2]["parent_value"] == "Great-Great-Grandchild - depth 4"
        assert deep_result[2]["depth"] == 5
        assert deep_result[2]["child_count"] == 0

        # Also check that Tag.get_lineage() works correctly for super deep tags:
        assert tag_5.get_lineage() == [
            'Root - depth 0',
            'Child - depth 1',
            'Grandchild - depth 2',
            'Great-Grandchild - depth 3',
            'Great-Great-Grandchild - depth 4',
            'Great-Great-Great-Grandchild - depth 5',
        ]

    def test_object_tags_deep(self) -> None:
        """
        Test that a deep taxonomy's tags can be used to tag objects
        """
        taxonomy = tagging_api.create_taxonomy(name="Deep Taxonomy")
        tag_0 = tagging_api.add_tag_to_taxonomy(taxonomy, "Bob - depth 0")
        tag_1 = tagging_api.add_tag_to_taxonomy(taxonomy, "Janet - depth 1", parent_tag_value=tag_0.value)
        tag_2 = tagging_api.add_tag_to_taxonomy(taxonomy, "Alice - depth 2", parent_tag_value=tag_1.value)
        tag_3 = tagging_api.add_tag_to_taxonomy(taxonomy, "Fred - depth 3", parent_tag_value=tag_2.value)
        tag_4 = tagging_api.add_tag_to_taxonomy(taxonomy, "Patty - depth 4", parent_tag_value=tag_3.value)
        tag_5 = tagging_api.add_tag_to_taxonomy(taxonomy, "Clara - depth 5", parent_tag_value=tag_4.value)
        assert tag_5.depth == 5

        object_id = "some object"
        tagging_api.tag_object(object_id, taxonomy, tags=[tag_5.value])

        assert [ot.tag for ot in tagging_api.get_object_tags(object_id)] == [tag_5]

        # Now test the sort order of many tags (the recursive CTE handles depth > 3 correctly):
        tagging_api.tag_object(object_id, taxonomy, tags=[
            tag_0.value, tag_1.value, tag_2.value, tag_3.value, tag_4.value, tag_5.value,
        ])
        assert [ot.tag for ot in tagging_api.get_object_tags(object_id)] == [
            tag_0,
            tag_1,
            tag_2,
            tag_3,
            tag_4,
            tag_5,
        ]

    def test_depth_limit(self) -> None:
        """
        Test that there is a limit to how deeply we can create tags:
        """
        taxonomy = tagging_api.create_taxonomy(name="Deep Taxonomy")
        tag_0 = tagging_api.add_tag_to_taxonomy(taxonomy, "Bob - depth 0")
        tag_1 = tagging_api.add_tag_to_taxonomy(taxonomy, "Janet - depth 1", parent_tag_value=tag_0.value)
        tag_2 = tagging_api.add_tag_to_taxonomy(taxonomy, "Alice - depth 2", parent_tag_value=tag_1.value)
        tag_3 = tagging_api.add_tag_to_taxonomy(taxonomy, "Fred - depth 3", parent_tag_value=tag_2.value)
        tag_4 = tagging_api.add_tag_to_taxonomy(taxonomy, "Clara - depth 4", parent_tag_value=tag_3.value)
        tag_4 = tagging_api.add_tag_to_taxonomy(taxonomy, "Patty - depth 5", parent_tag_value=tag_4.value)
        with pytest.raises(ValidationError, match="Cannot create tags more than 6 levels deep."):
            tagging_api.add_tag_to_taxonomy(taxonomy, "Deep - depth 6", parent_tag_value=tag_4.value)
