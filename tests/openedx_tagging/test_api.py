"""
Test the tagging APIs
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

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

    def test_get_tags_excludes_archived(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value__in=["Fungi", "Cnidaria"]).update(archived=True)
        values = [row["value"] for row in tagging_api.get_tags(self.taxonomy)]
        assert "Fungi" not in values
        assert "Cnidaria" not in values
        assert "Plantae" in values
        # The tags are still in the database, and parents no longer count them as children:
        assert self.taxonomy.tag_set.filter(value="Fungi").exists()
        assert "Eukaryota (children: 4)" in pretty_format_tags(tagging_api.get_tags(self.taxonomy), parent=False)

    def test_get_root_tags_excludes_archived(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Archaea").update(archived=True)
        assert pretty_format_tags(tagging_api.get_root_tags(self.taxonomy), parent=False) == [
            'Bacteria (children: 2)',
            'Eukaryota (children: 5)',
        ]

    def test_get_children_tags_excludes_archived(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Cnidaria").update(archived=True)
        values = [row["value"] for row in tagging_api.get_children_tags(self.taxonomy, "Animalia")]
        assert values == ["Arthropoda", "Chordata", "Ctenophora", "Gastrotrich", "Placozoa", "Porifera"]
        # The parent's own child count doesn't include the archived child either:
        children = pretty_format_tags(tagging_api.get_children_tags(self.taxonomy, "Eukaryota"), parent=False)
        assert "  Animalia (children: 6)" in children

    def test_get_children_tags_archived_parent(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Chordata").update(archived=True)
        with self.assertRaises(Tag.IsArchived):
            tagging_api.get_children_tags(self.taxonomy, "Chordata")
        with self.assertRaises(Tag.IsArchived):  # The full-depth query behaves the same way
            self.taxonomy.get_filtered_tags(parent_tag_value="Chordata")

    def test_archived_parent_error_is_a_does_not_exist_error(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Chordata").update(archived=True)
        # Existing handlers for a missing tag keep working for an archived one:
        with self.assertRaises(Tag.DoesNotExist):
            tagging_api.get_children_tags(self.taxonomy, "Chordata")
        # But a tag that never existed is not reported as archived:
        with self.assertRaises(Tag.DoesNotExist) as missing:
            tagging_api.get_children_tags(self.taxonomy, "No such tag")
        assert not isinstance(missing.exception, Tag.IsArchived)

    def test_search_tags_excludes_archived(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Eubacteria").update(archived=True)
        result = tagging_api.search_tags(self.taxonomy, search_term='eU')
        assert pretty_format_tags(result, parent=False) == [
            'Archaea (children: 1)',
            '  Euryarchaeida (children: 0)',
            'Eukaryota (children: 0)',
        ]

    def test_search_tags_exclude_object_ignores_archived_object_tag(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["Fungi"])
        values = [row["value"] for row in tagging_api.search_tags(self.taxonomy, "Fungi", exclude_object_id="obj1")]
        assert values == []  # Applied to the object, so it is excluded from its autocomplete
        ObjectTag.objects.filter(object_id="obj1").update(archived=True)
        values = [row["value"] for row in tagging_api.search_tags(self.taxonomy, "Fungi", exclude_object_id="obj1")]
        assert "Fungi" in values  # The ancestor "Eukaryota" is included too

    def test_archived_taxonomy_hides_its_tags(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        self.taxonomy.refresh_from_db()
        assert not list(tagging_api.get_tags(self.taxonomy))
        assert not list(tagging_api.get_root_tags(self.taxonomy))
        assert not list(tagging_api.search_tags(self.taxonomy, search_term='eU'))
        with self.assertRaises(Tag.DoesNotExist):
            tagging_api.get_children_tags(self.taxonomy, "Animalia")

    def test_get_taxonomy_excludes_archived(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        assert tagging_api.get_taxonomy(self.taxonomy.pk) is None

    def test_get_taxonomy_by_export_id_excludes_archived(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        assert tagging_api.get_taxonomy_by_export_id("life_on_earth") is None

    def test_get_taxonomies_excludes_archived(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        assert self.taxonomy not in tagging_api.get_taxonomies(enabled=None)
        assert self.taxonomy not in tagging_api.get_taxonomies()

    def test_get_tags_free_text_excludes_archived(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.free_text_taxonomy, tags=["keep", "hide"])
        ObjectTag.objects.filter(taxonomy=self.free_text_taxonomy, _value="hide").update(archived=True)
        assert [row["value"] for row in tagging_api.get_tags(self.free_text_taxonomy)] == ["keep"]

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

    def test_add_tag_to_object(self) -> None:
        """Adds the tag as a new ObjectTag and leaves the object's other tags alone."""
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        existing = ObjectTag.objects.create(object_id="biology101", taxonomy=self.taxonomy, tag=self.archaea)

        object_tag = tagging_api.add_tag_to_object("biology101", self.bacteria)

        assert object_tag.pk != existing.pk
        assert object_tag.tag_id == self.bacteria.id
        assert object_tag.taxonomy_id == self.taxonomy.id
        assert object_tag.object_id == "biology101"
        assert sorted(ObjectTag.objects.filter(object_id="biology101").values_list("tag_id", flat=True)) == sorted(
            [self.archaea.id, self.bacteria.id]
        )

    def test_add_tag_to_object_reuses_an_existing_object_tag_for_the_tag(self) -> None:
        existing = ObjectTag.objects.create(object_id="biology101", taxonomy=self.taxonomy, tag=self.archaea)

        object_tag = tagging_api.add_tag_to_object("biology101", self.archaea)

        assert object_tag.pk == existing.pk
        assert ObjectTag.objects.filter(object_id="biology101").count() == 1

    def test_add_tag_to_object_requires_a_taxonomy(self) -> None:
        tag = Tag(taxonomy=None, value="Orphan")

        with pytest.raises(ValueError) as excinfo:
            tagging_api.add_tag_to_object("biology101", tag)

        assert str(excinfo.value) == "Tag (Orphan) does not belong to a taxonomy."
        assert not ObjectTag.objects.filter(object_id="biology101").exists()

    def test_add_tag_to_object_no_multiple(self) -> None:
        """A single-valued taxonomy rejects a second tag, with the same message tag_object() gives."""
        assert not self.taxonomy.allow_multiple
        ObjectTag.objects.create(object_id="biology101", taxonomy=self.taxonomy, tag=self.archaea)

        with pytest.raises(ValueError) as add_excinfo:
            tagging_api.add_tag_to_object("biology101", self.bacteria)
        with pytest.raises(ValueError) as tag_object_excinfo:
            tagging_api.tag_object("biology101", self.taxonomy, [self.archaea.value, self.bacteria.value])

        assert "only allows one tag per object" in str(add_excinfo.value)
        assert str(add_excinfo.value) == str(tag_object_excinfo.value)
        assert list(ObjectTag.objects.filter(object_id="biology101").values_list("tag_id", flat=True)) == [
            self.archaea.id
        ]

    def test_add_tag_to_object_limit(self) -> None:
        """An object at the tag limit rejects another tag, with the same message tag_object() gives."""
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        self.create_100_taxonomies()

        with pytest.raises(ValueError) as add_excinfo:
            tagging_api.add_tag_to_object("limit_tag_count", self.archaea)
        with pytest.raises(ValueError) as tag_object_excinfo:
            tagging_api.tag_object("limit_tag_count", self.taxonomy, [self.archaea.value])

        assert "Cannot add more than 100 tags to (limit_tag_count)" in str(add_excinfo.value)
        assert str(add_excinfo.value) == str(tag_object_excinfo.value)
        assert not ObjectTag.objects.filter(object_id="limit_tag_count", taxonomy=self.taxonomy).exists()

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

    def test_get_object_tag_counts_excludes_archived(self) -> None:
        obj1, obj2 = "object_id1", "object_id2"
        tagging_api.tag_object(object_id=obj1, taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id=obj2, taxonomy=self.taxonomy, tags=["Chordata"])
        ObjectTag.objects.filter(object_id=obj1).update(archived=True)  # obj1's only association

        with self.assertNumQueries(1):
            assert tagging_api.get_object_tag_counts("object_*") == {obj2: 1}
        with self.assertNumQueries(1):
            assert tagging_api.get_object_tag_counts("object_*", count_implicit=True) == {obj2: 3}

    def test_add_usage_counts_excludes_archived(self) -> None:
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        tagging_api.tag_object(object_id="obj2", taxonomy=self.taxonomy, tags=["DPANN"])
        ObjectTag.objects.filter(object_id="obj2").update(archived=True)

        tag_data = tagging_api.get_children_tags(self.taxonomy, "Archaea")
        with self.assertNumQueries(2):  # One for the tag data and one for the usage counts
            result = tagging_api.add_usage_counts(self.taxonomy, tag_data)
        assert {row["value"]: row["usage_count"] for row in result} == {
            "DPANN": 1,
            "Euryarchaeida": 0,
            "Proteoarchaeota": 0,
        }

    def test_add_usage_counts_archived_only_association_is_zero(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])  # DPANN's only association
        ObjectTag.objects.filter(object_id="obj1").update(archived=True)

        tag_data = tagging_api.get_children_tags(self.taxonomy, "Archaea")
        with self.assertNumQueries(2):  # One for the tag data and one for the usage counts
            result = tagging_api.add_usage_counts(self.taxonomy, tag_data)
        assert {row["value"]: row["usage_count"] for row in result}["DPANN"] == 0

    def test_get_object_tags_excludes_archived_object_tag(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        ObjectTag.objects.filter(object_id="obj1").update(archived=True)
        assert not tagging_api.get_object_tags("obj1").exists()

    def test_get_object_tags_excludes_archived_tag(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        Tag.objects.filter(taxonomy=self.taxonomy, value="DPANN").update(archived=True)
        assert not ObjectTag.objects.get(object_id="obj1").archived  # Only the tag is archived
        assert not tagging_api.get_object_tags("obj1").exists()

    def test_get_object_tags_excludes_archived_taxonomy(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        assert not ObjectTag.objects.get(object_id="obj1").archived  # Neither the association...
        assert not get_tag("DPANN").archived  # ...nor the tag is archived
        assert not tagging_api.get_object_tags("obj1").exists()

    def test_get_object_tags_excludes_archived_free_text_taxonomy(self) -> None:
        # Only the taxonomy is archived: a free text object tag has no tag, so no other condition can hide it.
        self.free_text_taxonomy.enabled = True
        self.free_text_taxonomy.save()
        tagging_api.tag_object(object_id="obj1", taxonomy=self.free_text_taxonomy, tags=["free"])
        assert tagging_api.get_object_tags("obj1").exists()
        assert tagging_api.get_object_tag_counts("obj1") == {"obj1": 1}

        Taxonomy.objects.filter(pk=self.free_text_taxonomy.pk).update(archived=True)
        assert not ObjectTag.objects.get(object_id="obj1").archived
        assert not tagging_api.get_object_tags("obj1").exists()
        assert tagging_api.get_object_tag_counts("obj1") == {}

    def test_archived_records_still_resolve_through_foreign_keys(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        Tag.objects.filter(taxonomy=self.taxonomy, value="DPANN").update(archived=True)
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        object_tag = ObjectTag.objects.get(object_id="obj1")
        assert object_tag.tag is not None
        assert object_tag.tag.archived
        assert object_tag.taxonomy is not None
        assert object_tag.taxonomy.archived
        assert object_tag.value == "DPANN"

    def test_default_managers_include_archived(self) -> None:
        tagging_api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["DPANN"])
        tag = get_tag("DPANN")
        Tag.objects.filter(pk=tag.pk).update(archived=True)
        Taxonomy.objects.filter(pk=self.read_only_taxonomy.pk).update(archived=True)
        ObjectTag.objects.filter(object_id="obj1").update(archived=True)
        assert Tag.objects.filter(pk=tag.pk).exists()
        assert not Tag.objects.unarchived().filter(pk=tag.pk).exists()
        assert Taxonomy.objects.filter(pk=self.read_only_taxonomy.pk).exists()
        assert not Taxonomy.objects.unarchived().filter(pk=self.read_only_taxonomy.pk).exists()
        assert ObjectTag.objects.filter(object_id="obj1").exists()
        assert not ObjectTag.objects.unarchived().filter(object_id="obj1").exists()

    def test_update_tag_in_taxonomy_archived_tag(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Fungi").update(archived=True)
        with self.assertRaisesRegex(ValueError, "archived"):
            tagging_api.update_tag_in_taxonomy(self.taxonomy, "Fungi", "Mushrooms")
        assert get_tag("Fungi").value == "Fungi"

    def test_update_tag_in_taxonomy_archived_taxonomy(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        self.taxonomy.refresh_from_db()
        with self.assertRaisesRegex(ValueError, "archived"):
            tagging_api.update_tag_in_taxonomy(self.taxonomy, "Fungi", "Mushrooms")
        assert get_tag("Fungi").value == "Fungi"

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

    def test_add_tag_to_taxonomy_archived_taxonomy(self) -> None:
        Taxonomy.objects.filter(pk=self.taxonomy.pk).update(archived=True)
        self.taxonomy.refresh_from_db()
        tag_count = self.taxonomy.tag_set.count()
        with self.assertRaisesRegex(ValueError, "archived"):
            tagging_api.add_tag_to_taxonomy(self.taxonomy, "New tag")
        assert self.taxonomy.tag_set.count() == tag_count

    def test_add_tag_to_taxonomy_archived_parent(self) -> None:
        Tag.objects.filter(taxonomy=self.taxonomy, value="Animalia").update(archived=True)
        tag_count = self.taxonomy.tag_set.count()
        with self.assertRaisesRegex(ValueError, "archived"):
            tagging_api.add_tag_to_taxonomy(self.taxonomy, "New tag", parent_tag_value="Animalia")
        assert self.taxonomy.tag_set.count() == tag_count


@ddt.ddt
class TestDeletionLock(TestTagTaxonomyMixin, TestCase):
    """
    Tests for the deletion-lock API functions.
    """

    def _tag(self, object_id: str, value: str = "Mammalia", taxonomy: Taxonomy | None = None) -> ObjectTag:
        taxonomy = taxonomy or self.taxonomy
        tagging_api.tag_object(object_id=object_id, taxonomy=taxonomy, tags=[value])
        return ObjectTag.objects.get(object_id=object_id, taxonomy=taxonomy)

    def _is_locked(self, object_tag: ObjectTag) -> bool:
        return ObjectTag.objects.get(pk=object_tag.pk).deletion_locked

    def test_lock_single_id(self) -> None:
        object_tag = self._tag("obj1")
        assert not self._is_locked(object_tag)
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        assert self._is_locked(object_tag)

    def test_lock_is_idempotent(self) -> None:
        object_tag = self._tag("obj1")
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        with CaptureQueriesContext(connection) as ctx:
            tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        assert not any(q["sql"].startswith("UPDATE") for q in ctx.captured_queries)
        assert self._is_locked(object_tag)

    def test_lock_multiple_ids_in_one_call(self) -> None:
        object_tags = [self._tag("obj1"), self._tag("obj2"), self._tag("obj3", "Fungi")]
        with CaptureQueriesContext(connection) as ctx:
            tagging_api.lock_object_tags_for_deletion([object_tag.pk for object_tag in object_tags])
        assert sum(q["sql"].startswith("UPDATE") for q in ctx.captured_queries) == 1
        assert all(self._is_locked(object_tag) for object_tag in object_tags)

    def test_lock_with_missing_id_raises_and_locks_nothing(self) -> None:
        valid_tags = [self._tag("obj1"), self._tag("obj2")]
        missing_id = max(object_tag.pk for object_tag in valid_tags) + 1000
        with pytest.raises(ObjectTag.DoesNotExist, match=str(missing_id)):
            tagging_api.lock_object_tags_for_deletion([*(object_tag.pk for object_tag in valid_tags), missing_id])
        assert not any(self._is_locked(object_tag) for object_tag in valid_tags)

    def test_lock_raises_when_a_row_vanishes_before_the_write(self) -> None:
        object_tag = self._tag("obj1")
        real_update = QuerySet.update

        def delete_then_update(queryset: QuerySet[Any], **kwargs: Any) -> int:
            ObjectTag.objects.filter(pk=object_tag.pk).delete()
            return real_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", delete_then_update):
            with pytest.raises(ObjectTag.DoesNotExist, match=str(object_tag.pk)):
                tagging_api.lock_object_tags_for_deletion(object_tag.pk)

    def test_lock_tolerates_a_concurrent_lock(self) -> None:
        object_tag = self._tag("obj1")
        real_update = QuerySet.update

        def lock_then_update(queryset: QuerySet[Any], **kwargs: Any) -> int:
            real_update(ObjectTag.objects.filter(pk=object_tag.pk), deletion_locked=True)
            return real_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", lock_then_update):
            tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        assert self._is_locked(object_tag)

    @ddt.data("12", b"12", True, 1.5, None, ["1"], [1, "1"], [1.0], [None])
    def test_lock_rejects_non_int_ids(self, bad_ids: Any) -> None:
        self._tag("obj1")
        with pytest.raises(TypeError):
            tagging_api.lock_object_tags_for_deletion(bad_ids)
        assert not ObjectTag.objects.filter(deletion_locked=True).exists()

    def test_lock_empty_input_runs_no_queries(self) -> None:
        with self.assertNumQueries(0):
            tagging_api.lock_object_tags_for_deletion([])

    def test_lock_changes_only_deletion_locked(self) -> None:
        object_tag = self._tag("obj1")
        ObjectTag.objects.filter(pk=object_tag.pk).update(archived=True)
        fields = ("archived", "object_id", "taxonomy_id", "tag_id", "_value")
        before = ObjectTag.objects.filter(pk=object_tag.pk).values(*fields).get()
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        after = ObjectTag.objects.filter(pk=object_tag.pk).values(*fields).get()
        assert after == before
        assert after["archived"] is True
        assert self._is_locked(object_tag)

    def test_get_tags_locked_returns_only_locked_tags(self) -> None:
        tagging_api.lock_object_tags_for_deletion(self._tag("obj1", "Mammalia").pk)
        self._tag("obj2", "Fungi")
        tag_ids = [self.mammalia.pk, get_tag("Fungi").pk]
        with self.assertNumQueries(1):
            result = tagging_api.get_tags_locked_for_deletion(tag_ids)
        assert result == {self.mammalia.pk}

    def test_get_tags_locked_query_count_for_whole_taxonomy(self) -> None:
        locked_values = ["Mammalia", "Fungi", "Archaea"]
        for index, value in enumerate(locked_values):
            tagging_api.lock_object_tags_for_deletion(self._tag(f"obj{index}", value).pk)
        all_tag_ids = list(Tag.objects.filter(taxonomy=self.taxonomy).values_list("pk", flat=True))
        assert len(all_tag_ids) > len(locked_values)
        with self.assertNumQueries(1):
            result = tagging_api.get_tags_locked_for_deletion(all_tag_ids)
        assert result == {get_tag(value).pk for value in locked_values}

    def test_get_tags_locked_ignores_unknown_tag_ids_and_empty_input(self) -> None:
        tagging_api.lock_object_tags_for_deletion(self._tag("obj1", "Mammalia").pk)
        unknown = max(Tag.objects.values_list("pk", flat=True)) + 1000
        assert tagging_api.get_tags_locked_for_deletion([self.mammalia.pk, unknown]) == {self.mammalia.pk}
        with self.assertNumQueries(0):
            assert tagging_api.get_tags_locked_for_deletion([]) == set()

    def test_taxonomy_locked_once_an_association_is_locked(self) -> None:
        object_tag = self._tag("obj1", "Mammalia")
        assert not tagging_api.is_taxonomy_locked_for_deletion(self.taxonomy.pk)
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        assert tagging_api.is_taxonomy_locked_for_deletion(self.taxonomy.pk)

    def test_taxonomy_lock_does_not_lock_a_different_taxonomy(self) -> None:
        tagging_api.lock_object_tags_for_deletion(self._tag("obj1", "Mammalia").pk)
        assert not tagging_api.is_taxonomy_locked_for_deletion(self.read_only_taxonomy.pk)

    def test_taxonomy_locked_by_free_text_association(self) -> None:
        object_tag = self._tag("obj1", "my own words", taxonomy=self.free_text_taxonomy)
        assert object_tag.tag is None
        assert not tagging_api.is_taxonomy_locked_for_deletion(self.free_text_taxonomy.pk)
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        assert tagging_api.is_taxonomy_locked_for_deletion(self.free_text_taxonomy.pk)

    def test_taxonomy_none_is_not_locked(self) -> None:
        object_tag = self._tag("obj1")
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        Taxonomy.objects.filter(pk=self.taxonomy.pk).delete()
        survivor = ObjectTag.objects.get(pk=object_tag.pk)
        assert survivor.taxonomy_id is None
        assert survivor.deletion_locked
        with self.assertNumQueries(0):
            assert not tagging_api.is_taxonomy_locked_for_deletion(None)

    def test_archived_locked_association_still_counts(self) -> None:
        """
        Archiving a locked association does not stop it protecting its tag and taxonomy.

        Adding ``.unarchived()`` to either reader makes this test fail.
        """
        object_tag = self._tag("obj1")
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        ObjectTag.objects.filter(pk=object_tag.pk).update(archived=True)
        assert tagging_api.get_tags_locked_for_deletion([self.mammalia.pk]) == {self.mammalia.pk}
        assert tagging_api.is_taxonomy_locked_for_deletion(self.taxonomy.pk)


@ddt.ddt
class TestArchiveLockedObjectTags(TestTagTaxonomyMixin, TestCase):
    """
    Tests that deleting a deletion-locked ObjectTag archives it, and that re-applying the tag un-archives it.
    """

    def setUp(self) -> None:
        super().setUp()
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()

    def _apply(self, object_id: str, values: list[str], taxonomy: Taxonomy | None = None) -> None:
        tagging_api.tag_object(object_id=object_id, taxonomy=taxonomy or self.taxonomy, tags=values)

    def _object_tag(self, object_id: str, value: str, taxonomy: Taxonomy | None = None) -> ObjectTag:
        return ObjectTag.objects.get(object_id=object_id, taxonomy=taxonomy or self.taxonomy, _value=value)

    def _locked(self, object_id: str, value: str, taxonomy: Taxonomy | None = None) -> ObjectTag:
        object_tag = self._object_tag(object_id, value, taxonomy)
        tagging_api.lock_object_tags_for_deletion(object_tag.pk)
        return self._object_tag(object_id, value, taxonomy)

    def _query_count(self, func, *args) -> int:
        with CaptureQueriesContext(connection) as queries:
            func(*args)
        return len(queries)

    @ddt.data("taxonomy", "free_text_taxonomy")
    def test_tag_object_archives_locked_and_reapply_restores(self, taxonomy_attr: str) -> None:
        """
        AC: replacing tags archives the locked association and deletes the unlocked one; re-applying restores the row.
        """
        taxonomy = getattr(self, taxonomy_attr)
        self._apply("obj1", ["Mammalia", "Fungi"], taxonomy)
        locked = self._locked("obj1", "Mammalia", taxonomy)
        unlocked_pk = self._object_tag("obj1", "Fungi", taxonomy).pk

        self._apply("obj1", ["Archaea"], taxonomy)

        archived = ObjectTag.objects.get(pk=locked.pk)
        assert archived.archived is True
        assert archived.deletion_locked is True
        assert not ObjectTag.objects.filter(pk=unlocked_pk).exists()
        assert self._object_tag("obj1", "Archaea", taxonomy).archived is False
        assert [t.value for t in tagging_api.get_object_tags("obj1")] == ["Archaea"]

        # The lowercase re-apply is deliberate: the unique constraint is case-insensitive, so it must still match.
        self._apply("obj1", ["mammalia"], taxonomy)

        reapplied = self._object_tag("obj1", "Mammalia", taxonomy)
        assert reapplied.pk == locked.pk
        assert reapplied.archived is False
        assert reapplied.deletion_locked is True
        assert reapplied.value == ("mammalia" if taxonomy.allow_free_text else "Mammalia")
        assert ObjectTag.objects.filter(object_id="obj1", _value="Mammalia").count() == 1

    def test_delete_object_tags_archives_locked_and_reimport_restores(self) -> None:
        """
        AC: deleting a content object archives its locked tag and deletes the other; re-import restores the same row.
        """
        self._apply("obj1", ["Mammalia", "Fungi"])
        locked = self._locked("obj1", "Mammalia")
        unlocked_pk = self._object_tag("obj1", "Fungi").pk

        tagging_api.delete_object_tags("obj1")

        assert ObjectTag.objects.get(pk=locked.pk).archived is True
        assert not ObjectTag.objects.filter(pk=unlocked_pk).exists()
        assert not tagging_api.get_object_tags("obj1").exists()

        self._apply("obj1", ["Mammalia"])

        reimported = self._object_tag("obj1", "Mammalia")
        assert reimported.pk == locked.pk
        assert reimported.archived is False
        assert reimported.deletion_locked is True

    @ddt.data("association", "tag", "taxonomy")
    def test_delete_object_tags_keep_hidden_deletes_visible_and_leaves_hidden_untouched(self, hidden_by: str) -> None:
        if hidden_by == "taxonomy":
            self._apply("obj1", ["Fungi"])
            self._apply("obj1", ["hidden free text"], self.free_text_taxonomy)
            visible_pk = self._object_tag("obj1", "Fungi").pk
            hidden = self._object_tag("obj1", "hidden free text", self.free_text_taxonomy)
            Taxonomy.objects.filter(pk=self.free_text_taxonomy.pk).update(archived=True)
        else:
            self._apply("obj1", ["Fungi", "Mammalia"])
            visible_pk = self._object_tag("obj1", "Fungi").pk
            hidden = self._object_tag("obj1", "Mammalia")
            if hidden_by == "association":
                ObjectTag.objects.filter(pk=hidden.pk).update(archived=True)
            else:
                Tag.objects.filter(taxonomy=self.taxonomy, value="Mammalia").update(archived=True)
        assert [t.pk for t in tagging_api.get_object_tags("obj1")] == [visible_pk]
        hidden_before = ObjectTag.objects.get(pk=hidden.pk)

        tagging_api.delete_object_tags("obj1", keep_hidden=True)

        assert not ObjectTag.objects.filter(pk=visible_pk).exists()
        hidden_after = ObjectTag.objects.get(pk=hidden.pk)
        assert hidden_after.archived is hidden_before.archived
        assert hidden_after.deletion_locked is hidden_before.deletion_locked

    def test_delete_object_tags_keep_hidden_archives_visible_locked_and_deletes_visible_unlocked(self) -> None:
        self._apply("obj1", ["Mammalia", "Fungi"])
        locked = self._locked("obj1", "Mammalia")
        unlocked_pk = self._object_tag("obj1", "Fungi").pk

        tagging_api.delete_object_tags("obj1", keep_hidden=True)

        archived = ObjectTag.objects.get(pk=locked.pk)
        assert archived.archived is True
        assert archived.deletion_locked is True
        assert not ObjectTag.objects.filter(pk=unlocked_pk).exists()

    def test_delete_object_tags_keep_hidden_deletes_placeholder_association(self) -> None:
        tagging_api.tag_object("obj1", None, ["X"], create_invalid=True, taxonomy_export_id="missing-taxonomy")
        placeholder = ObjectTag.objects.get(object_id="obj1")
        assert placeholder.taxonomy is None
        assert placeholder.export_id == "missing-taxonomy"

        tagging_api.delete_object_tags("obj1", keep_hidden=True)

        assert not ObjectTag.objects.filter(pk=placeholder.pk).exists()

    def test_delete_object_tags_by_default_deletes_hidden_unlocked_association(self) -> None:
        self._apply("obj1", ["Mammalia", "Fungi"])
        hidden = self._object_tag("obj1", "Mammalia")
        ObjectTag.objects.filter(pk=hidden.pk).update(archived=True)

        tagging_api.delete_object_tags("obj1")

        assert not ObjectTag.objects.filter(object_id="obj1").exists()

    @ddt.data("taxonomy", "free_text_taxonomy")
    def test_copy_tags_archives_dropped_locked_copy_and_keeps_shared_one(self, taxonomy_attr: str) -> None:
        """
        AC: library sync archives a locked copy the source dropped and keeps one it still has; never copies archived.
        """
        taxonomy = getattr(self, taxonomy_attr)
        self._apply("source", ["Mammalia", "Fungi"], taxonomy)
        tagging_api.copy_tags("source", "dest")
        kept = self._locked("dest", "Mammalia", taxonomy)
        dropped = self._locked("dest", "Fungi", taxonomy)
        self._apply("source", ["Mammalia", "Archaea", "Chordata"], taxonomy)
        ObjectTag.objects.filter(object_id="source", _value="Chordata").update(archived=True)

        tagging_api.copy_tags("source", "dest")

        still_kept = self._object_tag("dest", "Mammalia", taxonomy)
        assert still_kept.pk == kept.pk
        assert still_kept.archived is False
        assert still_kept.is_copied is True
        assert still_kept.deletion_locked is True
        assert ObjectTag.objects.get(pk=dropped.pk).archived is True
        copied = self._object_tag("dest", "Archaea", taxonomy)
        assert copied.is_copied is True
        assert copied.archived is False
        assert not ObjectTag.objects.filter(object_id="dest", _value="Chordata").exists()

    @ddt.data("known_taxonomy", "no_taxonomy")
    def test_reimport_restores_unresolved_association(self, case: str) -> None:
        """
        AC: re-import restores the same row for an association whose tag can't be resolved.
        """
        taxonomy = self.taxonomy if case == "known_taxonomy" else None
        kwargs: dict[str, Any] = {"create_invalid": True} if taxonomy else {"taxonomy_export_id": "unknown_taxonomy"}
        tagging_api.tag_object("obj1", taxonomy, ["Not a real tag"], **kwargs)
        unresolved = ObjectTag.objects.get(object_id="obj1")
        tagging_api.lock_object_tags_for_deletion(unresolved.pk)

        tagging_api.delete_object_tags("obj1")
        assert ObjectTag.objects.get(pk=unresolved.pk).archived is True
        tagging_api.tag_object("obj1", taxonomy, ["Not a real tag"], **kwargs)

        reimported = ObjectTag.objects.get(object_id="obj1")
        assert reimported.pk == unresolved.pk
        assert reimported.archived is False
        assert reimported.deletion_locked is True

    def test_archived_associations_do_not_count_toward_tag_limit(self) -> None:
        """
        Archived associations don't count toward OBJECT_MAX_TAGS.
        """
        with patch("openedx_tagging.api.OBJECT_MAX_TAGS", 2):
            self._apply("obj1", ["Mammalia", "Fungi"])
            for value in ("Mammalia", "Fungi"):
                self._locked("obj1", value)
            self._apply("obj1", [])
            assert ObjectTag.objects.filter(object_id="obj1", archived=True).count() == 2

            self._apply("obj1", ["one", "two"], self.free_text_taxonomy)

            with pytest.raises(ValueError, match="Cannot add more than 2 tags"):
                self._apply("obj1", ["Tag 4"], self.import_taxonomy)

    @ddt.data(("Alpha", "alpha"), ("ß", "ss"), ("café", "cafe"))
    @ddt.unpack
    def test_reapply_follows_database_collation(self, first: str, second: str) -> None:
        """
        Re-applying a value restores the archived row exactly when the database considers the values equal.
        """
        taxonomy = self.free_text_taxonomy
        self._apply("obj1", [first], taxonomy)
        locked = self._locked("obj1", first, taxonomy)
        self._apply("obj1", [], taxonomy)
        same = ObjectTag.objects.filter(pk=locked.pk, _value=second).exists()
        known = {
            "sqlite": {("Alpha", "alpha"): True, ("ß", "ss"): False, ("café", "cafe"): False},
            "mysql": {("Alpha", "alpha"): True, ("café", "cafe"): True},  # ß is left out: not sure of its collation
        }
        if (first, second) in known.get(connection.vendor, {}):
            assert same is known[connection.vendor][(first, second)]

        self._apply("obj1", [second], taxonomy)

        if same:
            reapplied = ObjectTag.objects.get(object_id="obj1")
            assert reapplied.pk == locked.pk
            assert reapplied.value == second
            assert reapplied.archived is False
            assert reapplied.deletion_locked is True
        else:
            still_archived = ObjectTag.objects.get(pk=locked.pk)
            assert still_archived.value == first
            assert still_archived.archived is True
            new_tag = ObjectTag.objects.get(object_id="obj1", archived=False)
            assert new_tag.pk != locked.pk
            assert new_tag.value == second
            assert new_tag.deletion_locked is False

    def test_reapply_without_taxonomy_prefers_locked_match(self) -> None:
        """
        With several matching associations, re-applying reuses the locked one and removes the others.
        """
        kwargs: dict[str, Any] = {"taxonomy_export_id": "gone"}
        tagging_api.tag_object("obj1", None, ["Alpha", "alpha"], **kwargs)  # No unique constraint without a taxonomy
        assert ObjectTag.objects.filter(object_id="obj1").count() == 2
        # Pick by exact spelling in Python, since filtering on _value would match both rows.
        locked = next(t for t in ObjectTag.objects.filter(object_id="obj1") if t.value == "alpha")
        tagging_api.lock_object_tags_for_deletion(locked.pk)

        tagging_api.tag_object("obj1", None, ["ALPHA"], **kwargs)

        reapplied = ObjectTag.objects.get(object_id="obj1")
        assert reapplied.pk == locked.pk
        assert reapplied.value == "ALPHA"
        assert reapplied.archived is False
        assert reapplied.deletion_locked is True

    def test_reapply_two_values_for_one_row_fails_atomically(self) -> None:
        """
        Two values that the database treats as equal can't both be applied, and nothing is written.
        """
        self._apply("obj1", ["Alpha"], self.free_text_taxonomy)
        locked = self._locked("obj1", "Alpha", self.free_text_taxonomy)
        self._apply("obj1", [], self.free_text_taxonomy)

        with pytest.raises(ValidationError):
            self._apply("obj1", ["Alpha", "alpha"], self.free_text_taxonomy)

        only_row = ObjectTag.objects.get(object_id="obj1")
        assert only_row.pk == locked.pk
        assert only_row.value == "Alpha"
        assert only_row.archived is True

    def test_reapplying_with_stale_value_ends_unarchived(self) -> None:
        """Re-applying an archived row whose _value is out of date still ends unarchived in the database."""
        self._apply("obj1", ["Mammalia"])
        locked = self._locked("obj1", "Mammalia")
        self._apply("obj1", [])
        self.taxonomy.update_tag("Mammalia", "Therians")  # Unlike the API function, this doesn't resync ObjectTags
        assert ObjectTag.objects.filter(pk=locked.pk, _value="Mammalia").exists()  # The cached value is stale now

        self._apply("obj1", ["Therians"])

        reapplied = ObjectTag.objects.get(pk=locked.pk)
        assert reapplied.archived is False
        assert reapplied.value == "Therians"
        assert reapplied.deletion_locked is True

    @ddt.data("association", "tag")
    def test_tag_object_keeps_hidden_associations(self, hidden_by: str) -> None:
        """
        AC: saving a block's tags, changing only the visible ones, leaves an association the author cannot see alone.
        """
        self._apply("obj1", ["Mammalia", "Fungi"])
        hidden = self._object_tag("obj1", "Mammalia")
        if hidden_by == "association":
            ObjectTag.objects.filter(pk=hidden.pk).update(archived=True)
        else:
            Tag.objects.filter(taxonomy=self.taxonomy, value="Mammalia").update(archived=True)
        assert [t.value for t in tagging_api.get_object_tags("obj1")] == ["Fungi"]

        self._apply("obj1", ["Archaea"])

        assert [t.value for t in tagging_api.get_object_tags("obj1")] == ["Archaea"]
        assert ObjectTag.objects.filter(object_id="obj1", _value="Mammalia").count() == 1
        still_hidden = ObjectTag.objects.get(pk=hidden.pk)
        assert still_hidden.archived is (hidden_by == "association")
        assert still_hidden.deletion_locked is False

    def test_tag_object_unarchives_unlocked_hidden_association_on_reapply(self) -> None:
        self._apply("obj1", ["Mammalia"])
        hidden = self._object_tag("obj1", "Mammalia")
        ObjectTag.objects.filter(pk=hidden.pk).update(archived=True)

        self._apply("obj1", ["Mammalia", "Fungi"])

        reapplied = ObjectTag.objects.get(pk=hidden.pk)
        assert reapplied.archived is False
        assert sorted(t.value for t in tagging_api.get_object_tags("obj1")) == ["Fungi", "Mammalia"]

    def test_tag_object_query_count_is_constant(self) -> None:
        """`tag_object()` removing a mixed set of tags runs the same queries however many tags it removes."""
        counts = []
        for object_id, size in (("small", 2), ("large", 20)):
            values = [f"{object_id} {index}" for index in range(size)]
            self._apply(object_id, values, self.free_text_taxonomy)
            for value in values[::2]:
                self._locked(object_id, value, self.free_text_taxonomy)
            counts.append(self._query_count(self._apply, object_id, [], self.free_text_taxonomy))
        assert counts[0] == counts[1]
