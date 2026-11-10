"""
Test the tagging base models
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import ddt  # type: ignore[import]
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test.testcases import TestCase

from openedx_tagging import api
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from openedx_tagging.models.utils import RESERVED_TAG_CHARS, TAG_EXTERNAL_ID_MAX_LENGTH, tag_external_id_candidate
from openedx_tagging.signal_handlers import _is_explicit_tag_delete
from openedx_tagging.tasks import (
    emit_content_object_associations_changed_for_object_ids_task,
    emit_content_object_associations_changed_for_tag_task,
)

from .utils import pretty_format_tags


def get_tag(value):
    """
    Fetches and returns the tag with the given value.
    """
    return Tag.objects.get(value=value)


class TestTagTaxonomyMixin:
    """
    Base class that uses the taxonomy fixture to load a base taxonomy and tags for testing.
    """

    fixtures = ["tests/openedx_tagging/fixtures/tagging.yaml"]

    def setUp(self):
        super().setUp()
        # Core pre-defined taxonomies for testing:
        self.taxonomy = Taxonomy.objects.get(name="Life on Earth")
        self.read_only_taxonomy = Taxonomy.objects.get(name="Read Only Taxonomy")
        self.free_text_taxonomy = api.create_taxonomy(name="Free Text", allow_free_text=True)
        self.import_taxonomy = Taxonomy.objects.get(name="Import Taxonomy Test")

        # References to some tags:
        self.archaea = get_tag("Archaea")
        self.archaebacteria = get_tag("Archaebacteria")
        self.bacteria = get_tag("Bacteria")
        self.eubacteria = get_tag("Eubacteria")
        self.chordata = get_tag("Chordata")
        self.mammalia = get_tag("Mammalia")
        self.animalia = get_tag("Animalia")
        self.read_only_taxonomy_tag = get_tag("System Tag 1")
        self.user_1 = get_user_model()(
            id=1,
            username="test_user_1",
        )
        self.user_1.save()
        self.user_2 = get_user_model()(
            id=2,
            username="test_user_2",
        )
        self.user_2.save()

        # Biology tags that contains 'eu'
        self.filtered_tags = [
            get_tag("Eubacteria"),
            get_tag("Eukaryota"),
            get_tag("Euryarchaeida"),
        ]

        self.system_tags = [
            get_tag("System Tag 1"),
            get_tag("System Tag 2"),
            get_tag("System Tag 3"),
            get_tag("System Tag 4"),
        ]

    def create_sort_test_taxonomy(self) -> Taxonomy:
        """
        Helper method to create a taxonomy that's difficult to sort correctly in tree order.
        """
        # pylint: disable=unused-variable
        taxonomy = api.create_taxonomy("Sort Test")

        root1 = Tag.objects.create(taxonomy=taxonomy, value="1")
        child1_1 = Tag.objects.create(taxonomy=taxonomy, value="11", parent=root1)
        child1_2 = Tag.objects.create(taxonomy=taxonomy, value="2", parent=root1)
        child1_3 = Tag.objects.create(taxonomy=taxonomy, value="1 A", parent=root1)
        child1_4 = Tag.objects.create(taxonomy=taxonomy, value="11111", parent=root1)
        grandchild1_4_1 = Tag.objects.create(taxonomy=taxonomy, value="1111-grandchild", parent=child1_4)

        root2 = Tag.objects.create(taxonomy=taxonomy, value="111")
        child2_1 = Tag.objects.create(taxonomy=taxonomy, value="11111111", parent=root2)
        child2_2 = Tag.objects.create(taxonomy=taxonomy, value="123", parent=root2)

        root1 = Tag.objects.create(taxonomy=taxonomy, value="ALPHABET")
        child1_1 = Tag.objects.create(taxonomy=taxonomy, value="Android", parent=root1)
        child1_2 = Tag.objects.create(taxonomy=taxonomy, value="abacus", parent=root1)
        child1_2 = Tag.objects.create(taxonomy=taxonomy, value="azure", parent=root1)
        child1_3 = Tag.objects.create(taxonomy=taxonomy, value="aardvark", parent=root1)
        child1_4 = Tag.objects.create(taxonomy=taxonomy, value="ANVIL", parent=root1)

        root2 = Tag.objects.create(taxonomy=taxonomy, value="abstract")
        child2_1 = Tag.objects.create(taxonomy=taxonomy, value="Andes", parent=root2)
        child2_2 = Tag.objects.create(taxonomy=taxonomy, value="azores islands", parent=root2)
        return taxonomy

    def create_100_taxonomies(self):
        """
        Helper method to create 100 taxonomies and to apply a tag from each to an object
        """
        dummy_taxonomies = []
        for i in range(100):
            taxonomy = api.create_taxonomy(
                name=f"ZZ Dummy Taxonomy {i:03}",
                allow_free_text=True,
                allow_multiple=True,
            )
            ObjectTag.objects.create(
                object_id="limit_tag_count",
                taxonomy=taxonomy,
                _export_id=taxonomy.export_id,
                _value="Dummy Tag",
            )
            dummy_taxonomies.append(taxonomy)
        return dummy_taxonomies


class ObjectTagTestSubclass(ObjectTag):
    """
    Model for testing the ObjectTag copy.
    """

    class Meta:
        managed = False
        proxy = True
        app_label = "oel_tagging"


@ddt.ddt
class TestTagTaxonomy(TestTagTaxonomyMixin, TestCase):
    """
    Test the Tag and Taxonomy models' properties and methods.
    """

    def test_read_only(self):
        assert not self.taxonomy.read_only
        assert self.read_only_taxonomy.read_only

    def test_read_only_taxonomy_tags_immutable(self):
        """
        The tags of a read-only taxonomy cannot be added, edited, or deleted.
        """
        with pytest.raises(ValueError) as add_exc:
            self.read_only_taxonomy.add_tag("New Tag")
        assert "read-only" in str(add_exc.value)

        with pytest.raises(ValueError) as update_exc:
            self.read_only_taxonomy.update_tag("System Tag 1", "Renamed")
        assert "read-only" in str(update_exc.value)

        with pytest.raises(ValueError) as delete_exc:
            self.read_only_taxonomy.delete_tags(["System Tag 1"])
        assert "read-only" in str(delete_exc.value)

    def test_representations(self):
        assert (
            str(self.taxonomy) == repr(self.taxonomy) == "<Taxonomy> (1) Life on Earth"
        )
        assert (
            str(self.read_only_taxonomy)
            == repr(self.read_only_taxonomy)
            == "<Taxonomy> (4) Read Only Taxonomy"
        )
        assert str(self.bacteria) == repr(self.bacteria) == "<Tag> (1) Bacteria"

    def test_unique_tags(self):
        # Creating new tag
        Tag(
            taxonomy=self.taxonomy,
            value='New value'
        ).save()

        # Creating repeated tag
        with self.assertRaises(IntegrityError):
            Tag(
                taxonomy=self.taxonomy,
                value=self.archaea.value,
            ).save()

    @ddt.data(
        # Root tags just return their own value
        ("bacteria", ["Bacteria"]),
        # Second level tags return two levels
        ("eubacteria", ["Bacteria", "Eubacteria"]),
        # Third level tags return three levels
        ("chordata", ["Eukaryota", "Animalia", "Chordata"]),
        # Even fourth level tags work
        ("mammalia", ["Eukaryota", "Animalia", "Chordata", "Mammalia"]),
    )
    @ddt.unpack
    def test_get_lineage(self, tag_attr, lineage):
        assert getattr(self, tag_attr).get_lineage() == lineage

    def test_trailing_whitespace(self):
        """
        Test that tags automatically strip out trailing/leading whitespace
        """
        t = self.taxonomy.add_tag(" white space  ")
        assert t.value == "white space"
        # And via the API:
        t2 = api.add_tag_to_taxonomy(self.taxonomy, "\t value\n")
        assert t2.value == "value"

    def test_reserved_chars(self):
        for reserved_char in RESERVED_TAG_CHARS:
            with pytest.raises(ValidationError):
                self.taxonomy.add_tag(f"tag 1 {reserved_char} tag 2")
            # And via the API:
            with pytest.raises(ValidationError):
                api.add_tag_to_taxonomy(self.taxonomy, f"tag 3 {reserved_char} tag 4")

    @ddt.data(
        ("test"),
        ("lightcast"),
        ("lightcast-skills"),
        ("io.lightcast.open-skills"),
        ("-3_languages"),
        ("LIGHTCAST_V17"),
        ("liGhtCaST"),
        ("日本"),
        ("Québec"),
        ("123456789"),
    )
    def test_export_id_format_valid(self, export_id):
        self.taxonomy.export_id = export_id
        self.taxonomy.full_clean()

    @ddt.data(
        ("LightCast Skills"),
        ("One,Two,Three"),
        (" "),
        ("Foo:Bar"),
        ("X;Y;Z"),
        ('"quotes"'),
        (" test"),
    )
    def test_export_id_format_invalid(self, export_id):
        self.taxonomy.export_id = export_id
        with pytest.raises(ValidationError):
            self.taxonomy.full_clean()

    def test_unique_export_id(self):
        # Valid
        self.taxonomy.export_id = 'test_1'
        self.free_text_taxonomy.export_id = 'test_2'
        self.taxonomy.save()
        self.free_text_taxonomy.save()

        # Invalid
        self.taxonomy.export_id = 'test_1'
        self.free_text_taxonomy.export_id = 'test_1'
        self.taxonomy.save()
        with pytest.raises(IntegrityError):
            self.free_text_taxonomy.save()


@ddt.ddt
class TestFilteredTagsClosedTaxonomy(TestTagTaxonomyMixin, TestCase):
    """
    Test the the get_filtered_tags() method of closed taxonomies
    """
    def test_get_root(self) -> None:
        """
        Test basic retrieval of root tags in the closed taxonomy, using
        get_filtered_tags().
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1))
        common_fields = {"depth": 0, "parent_value": None}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the root tags, in alphabetical order. The fixture gives each tag an
            # external_id equal to its own value.
            {"value": "Archaea", "child_count": 3, "external_id": "Archaea", **common_fields},
            {"value": "Bacteria", "child_count": 2, "external_id": "Bacteria", **common_fields},
            {"value": "Eukaryota", "child_count": 5, "external_id": "Eukaryota", **common_fields},
        ]

    def test_get_child_tags_one_level(self) -> None:
        """
        Test basic retrieval of tags one level below the "Eukaryota" root tag in
        the closed taxonomy, using get_filtered_tags(). With counts included.
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, parent_tag_value="Eukaryota"))
        common_fields = {"depth": 1, "parent_value": "Eukaryota"}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the Eukaryota tags, in alphabetical order. The fixture gives each tag an
            # external_id equal to its own value.
            {"value": "Animalia", "child_count": 7, "external_id": "Animalia", **common_fields},
            {"value": "Fungi", "child_count": 0, "external_id": "Fungi", **common_fields},
            {"value": "Monera", "child_count": 0, "external_id": "Monera", **common_fields},
            {"value": "Plantae", "child_count": 0, "external_id": "Plantae", **common_fields},
            {"value": "Protista", "child_count": 0, "external_id": "Protista", **common_fields},
        ]

    def test_get_grandchild_tags_one_level(self) -> None:
        """
        Test basic retrieval of a single level of tags at two level belows the
        "Eukaryota" root tag in the closed taxonomy, using get_filtered_tags().
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, parent_tag_value="Animalia"))
        common_fields = {"depth": 2, "parent_value": "Animalia"}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the Eukaryota tags, in alphabetical order. The fixture gives each tag an
            # external_id equal to its own value.
            {"value": "Arthropoda", "child_count": 0, "external_id": "Arthropoda", **common_fields},
            {"value": "Chordata", "child_count": 1, "external_id": "Chordata", **common_fields},
            {"value": "Cnidaria", "child_count": 0, "external_id": "Cnidaria", **common_fields},
            {"value": "Ctenophora", "child_count": 0, "external_id": "Ctenophora", **common_fields},
            {"value": "Gastrotrich", "child_count": 0, "external_id": "Gastrotrich", **common_fields},
            {"value": "Placozoa", "child_count": 0, "external_id": "Placozoa", **common_fields},
            {"value": "Porifera", "child_count": 0, "external_id": "Porifera", **common_fields},
        ]

    def test_get_depth_1_search_term(self) -> None:
        """
        Filter the root tags to only those that match a search term
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, search_term="ARCH"))
        assert result == [
            {
                "value": "Archaea",
                "child_count": 3,
                "depth": 0,
                "parent_value": None,
                "external_id": "Archaea",  # The fixture gives this tag an external_id equal to its own value
                "_id": 2,  # These IDs are hard-coded in the test fixture file
            },
        ]
        # Note that other tags in the taxonomy match "ARCH" but are excluded because of the depth=1 search

    def test_get_depth_1_child_search_term(self) -> None:
        """
        Filter the child tags of "Bacteria" to only those that match a search term
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, search_term="ARCH", parent_tag_value="Bacteria"))
        assert result == [
            {
                "value": "Archaebacteria",
                "child_count": 0,
                "depth": 1,
                "parent_value": "Bacteria",
                "external_id": "Archaebacteria",  # The fixture gives this tag an external_id equal to its own value
                "_id": 5,  # These IDs are hard-coded in the test fixture file
            },
        ]
        # Note that other tags in the taxonomy match "ARCH" but are excluded because of the depth=1 search

    def test_depth_1_queries(self) -> None:
        """
        Test the number of queries used by get_filtered_tags() with closed
        taxonomies when depth=1. This should be a constant, not O(n).
        """
        with self.assertNumQueries(1):
            self.test_get_root()
        with self.assertNumQueries(1):
            self.test_get_depth_1_search_term()
        # When listing the tags below a specific tag, there is one additional query to load the parent tag:
        with self.assertNumQueries(2):
            self.test_get_child_tags_one_level()
        with self.assertNumQueries(2):
            self.test_get_depth_1_child_search_term()
        with self.assertNumQueries(2):
            self.test_get_grandchild_tags_one_level()

    ##################

    def test_get_all(self) -> None:
        """
        Test getting all of the tags in the taxonomy, using get_filtered_tags()
        """
        result = pretty_format_tags(self.taxonomy.get_filtered_tags())
        assert result == [
            "Archaea (None) (children: 3)",
            "  DPANN (Archaea) (children: 0)",
            "  Euryarchaeida (Archaea) (children: 0)",
            "  Proteoarchaeota (Archaea) (children: 0)",
            "Bacteria (None) (children: 2)",
            "  Archaebacteria (Bacteria) (children: 0)",
            "  Eubacteria (Bacteria) (children: 0)",
            "Eukaryota (None) (children: 5)",
            "  Animalia (Eukaryota) (children: 7)",
            "    Arthropoda (Animalia) (children: 0)",
            "    Chordata (Animalia) (children: 1)",
            "      Mammalia (Chordata) (children: 0)",
            "    Cnidaria (Animalia) (children: 0)",
            "    Ctenophora (Animalia) (children: 0)",
            "    Gastrotrich (Animalia) (children: 0)",
            "    Placozoa (Animalia) (children: 0)",
            "    Porifera (Animalia) (children: 0)",
            "  Fungi (Eukaryota) (children: 0)",
            "  Monera (Eukaryota) (children: 0)",
            "  Plantae (Eukaryota) (children: 0)",
            "  Protista (Eukaryota) (children: 0)",
        ]

    def test_search(self) -> None:
        """
        Search the whole taxonomy (up to max depth) for a given term. Should
        return all tags that match the term as well as their ancestors.
        """
        result = pretty_format_tags(self.taxonomy.get_filtered_tags(search_term="ARCH"))
        assert result == [
            "Archaea (None) (children: 2)",  # Matches the value of this root tag, ARCHaea
            "  Euryarchaeida (Archaea) (children: 0)",  # Matches the value of this child tag
            "  Proteoarchaeota (Archaea) (children: 0)",  # Matches the value of this child tag
            "Bacteria (None) (children: 1)",  # Does not match this tag but matches a descendant:
            "  Archaebacteria (Bacteria) (children: 0)",  # Matches the value of this child tag
        ]

    def test_search_2(self) -> None:
        """
        Another search test, that matches a tag deeper in the taxonomy to check
        that all its ancestors are returned by the search.
        """
        result = pretty_format_tags(self.taxonomy.get_filtered_tags(search_term="chordata"))
        assert result == [
            "Eukaryota (None) (children: 1)",  # Has one child that matches
            "  Animalia (Eukaryota) (children: 1)",
            "    Chordata (Animalia) (children: 0)",  # this is the matching tag.
        ]

    def test_search_3(self) -> None:
        """
        Another search test, that matches a tag deeper in the taxonomy to check
        that the correct child_count is returned by the search.
        """
        result = pretty_format_tags(self.taxonomy.get_filtered_tags(search_term="RO"))
        assert result == [
            "Archaea (None) (children: 1)",
            "  Proteoarchaeota (Archaea) (children: 0)",
            "Eukaryota (None) (children: 2)",  # 2 direct matching children
            "  Animalia (Eukaryota) (children: 2)",  # also 2 matching children
            "    Arthropoda (Animalia) (children: 0)",  # match
            "    Gastrotrich (Animalia) (children: 0)",  # match
            "  Protista (Eukaryota) (children: 0)",  # match
        ]

    def test_tags_deep(self) -> None:
        """
        Test getting a deep tag in the taxonomy
        """
        result = list(self.taxonomy.get_filtered_tags(parent_tag_value="Chordata"))
        assert result == [
            {
                "value": "Mammalia",
                "parent_value": "Chordata",
                "depth": 3,
                "child_count": 0,
                "external_id": "Mammalia",  # The fixture gives this tag an external_id equal to its own value
                "_id": 21,  # These IDs are hard-coded in the test fixture file
            }
        ]

    def test_deep_queries(self) -> None:
        """
        Test the number of queries used by get_filtered_tags() with closed
        taxonomies when depth=None. This should be a constant, not O(n).
        """
        with self.assertNumQueries(1):
            self.test_get_all()
        # Searching below a specific tag requires an additional query to load that tag:
        with self.assertNumQueries(2):
            self.test_tags_deep()
        # Keyword search requires an additional query:
        with self.assertNumQueries(2):
            self.test_search()
        with self.assertNumQueries(2):
            self.test_search_2()

    def test_get_external_id(self) -> None:
        """
        Test that if our tags have external IDs, those external IDs are returned
        """
        self.bacteria.external_id = "bct001"
        self.bacteria.save()
        result = list(self.taxonomy.get_filtered_tags(search_term="Eubacteria"))
        assert result[0]["value"] == "Bacteria"
        assert result[0]["external_id"] == "bct001"

    def test_tree_sort(self) -> None:
        """
        Verify that taxonomies can be sorted correctly in tree orer (case insensitive).

        The taxonomy used contains values that are tricky to sort correctly unless the tree sort algorithm is correct.
        """
        taxonomy = self.create_sort_test_taxonomy()
        result = pretty_format_tags(taxonomy.get_filtered_tags())
        assert result == [
            "1 (None) (children: 4)",
            "  1 A (1) (children: 0)",
            "  11 (1) (children: 0)",
            "  11111 (1) (children: 1)",
            "    1111-grandchild (11111) (children: 0)",
            "  2 (1) (children: 0)",
            "111 (None) (children: 2)",
            "  11111111 (111) (children: 0)",
            "  123 (111) (children: 0)",
            "abstract (None) (children: 2)",
            "  Andes (abstract) (children: 0)",
            "  azores islands (abstract) (children: 0)",
            "ALPHABET (None) (children: 5)",
            "  aardvark (ALPHABET) (children: 0)",
            "  abacus (ALPHABET) (children: 0)",
            "  Android (ALPHABET) (children: 0)",
            "  ANVIL (ALPHABET) (children: 0)",
            "  azure (ALPHABET) (children: 0)",
        ]


class TestFilteredTagsFreeTextTaxonomy(TestCase):
    """
    Tests for listing/autocompleting/searching for tags in a free text taxonomy.

    Free text taxonomies only return tags that are actually used.
    """

    def setUp(self):
        super().setUp()
        self.taxonomy = api.create_taxonomy(allow_free_text=True, name="FreeText")
        # The "triple" tag will be applied to three objects, "double" to two, and "solo" to one:
        api.tag_object(object_id="obj1", taxonomy=self.taxonomy, tags=["triple"])
        api.tag_object(object_id="obj2", taxonomy=self.taxonomy, tags=["triple", "double"])
        api.tag_object(object_id="obj3", taxonomy=self.taxonomy, tags=["triple", "double"])
        api.tag_object(object_id="obj4", taxonomy=self.taxonomy, tags=["solo"])

    def test_get_filtered_tags(self):
        """
        Test basic retrieval of all tags in the taxonomy.
        """
        result = list(self.taxonomy.get_filtered_tags())
        common_fields = {"child_count": 0, "depth": 0, "parent_value": None, "external_id": None, "_id": None}
        assert result == [
            # These should appear in alphabetical order:
            {"value": "double", **common_fields},
            {"value": "solo", **common_fields},
            {"value": "triple", **common_fields},
        ]

    def test_get_filtered_tags_num_queries(self):
        """
        Test that the number of queries used by get_filtered_tags() is fixed
        and not O(n) or worse.
        """
        with self.assertNumQueries(1):
            self.test_get_filtered_tags()

    def test_get_filtered_tags_with_search(self) -> None:
        """
        Test basic retrieval of only matching tags.
        """
        result1 = list(self.taxonomy.get_filtered_tags(search_term="le"))
        common_fields = {"child_count": 0, "depth": 0, "parent_value": None, "external_id": None, "_id": None}
        assert result1 == [
            # These should appear in alphabetical order:
            {"value": "double", **common_fields},
            {"value": "triple", **common_fields},
        ]
        # And it should be case insensitive:
        result2 = list(self.taxonomy.get_filtered_tags(search_term="LE"))
        assert result1 == result2


class TestObjectTag(TestTagTaxonomyMixin, TestCase):
    """
    Test the ObjectTag model and the related Taxonomy methods and fields.
    """

    def setUp(self):
        super().setUp()
        self.tag = self.bacteria
        self.object_tag = ObjectTag.objects.create(
            object_id="object:id:1",
            taxonomy=self.taxonomy,
            tag=self.tag,
        )

    def test_representations(self):
        assert (
            str(self.object_tag)
            == repr(self.object_tag)
            == "<ObjectTag> object:id:1: Life on Earth=Bacteria"
        )

    def test_cast(self):
        copy_tag = ObjectTagTestSubclass.cast(self.object_tag)
        assert (
            str(copy_tag)
            == repr(copy_tag)
            == "<ObjectTagTestSubclass> object:id:1: Life on Earth=Bacteria"
        )

    def test_object_tag_export_id(self):
        # ObjectTag's export_id defaults to its taxonomy's export_id
        assert self.object_tag.export_id == self.taxonomy.export_id

        # Even if we overwrite the export_id, it still uses the taxonomy's export_id
        self.object_tag.export_id = "another-taxonomy"
        assert self.object_tag.export_id == self.taxonomy.export_id
        self.object_tag.save()
        assert self.object_tag.export_id == self.taxonomy.export_id

        # But if the taxonomy is deleted, then the object_tag's export_id reverts to our cached export_id.
        # Patch explicit-delete detection because this test is about ObjectTag fallback behavior,
        # not tag deletion event-origins.
        with patch("openedx_tagging.signal_handlers._is_explicit_tag_delete", return_value=False):
            self.taxonomy.delete()
        self.object_tag.refresh_from_db()
        assert self.object_tag.export_id == "another-taxonomy"

    def test_is_explicit_tag_delete_taxonomy_cascade(self):
        # Root-level tag is treated as explicit when cascaded from a Taxonomy deletion,
        # so its handler covers the whole subtree via lineage__startswith.
        assert _is_explicit_tag_delete(
            instance=self.tag, origin=cast(Any, self.taxonomy), using="default"
        )
        # Non-root tag returns False — its ancestor's handler already covers it.
        assert not _is_explicit_tag_delete(
            instance=self.eubacteria, origin=cast(Any, self.taxonomy), using="default"
        )

    def test_object_tag_value(self):
        # ObjectTag's value defaults to its tag's value
        object_tag = ObjectTag.objects.create(
            object_id="object:id",
            taxonomy=self.taxonomy,
            tag=self.tag,
        )
        assert object_tag.value == self.tag.value

        # Even if we overwrite the value, it still uses the tag's value
        object_tag.value = "Another tag"
        assert object_tag.value == self.tag.value
        object_tag.save()
        assert object_tag.value == self.tag.value

        # But if the tag is deleted, then the object_tag's value reverts to our cached value
        self.tag.delete()
        object_tag.refresh_from_db()
        assert object_tag.value == "Another tag"

    def test_object_tag_lineage(self):
        # ObjectTag's value defaults to its tag's lineage
        object_tag = ObjectTag.objects.create(
            object_id="object:id",
            taxonomy=self.taxonomy,
            tag=self.tag,
        )
        assert object_tag.get_lineage() == self.tag.get_lineage()

        # Even if we overwrite the value, it still uses the tag's lineage
        object_tag.value = "Another tag"
        assert object_tag.get_lineage() == self.tag.get_lineage()
        object_tag.save()
        assert object_tag.get_lineage() == self.tag.get_lineage()

        # But if the tag is deleted, then the object_tag's lineage reverts to our cached value
        self.tag.delete()
        object_tag.refresh_from_db()
        assert object_tag.get_lineage() == ["Another tag"]

    def test_validate_value_free_text(self):
        assert self.free_text_taxonomy.allow_free_text
        # An empty string or other non-string is not valid in a free-text taxonomy
        assert self.free_text_taxonomy.validate_value("") is False
        assert self.free_text_taxonomy.validate_value(None) is False
        assert self.free_text_taxonomy.validate_value(True) is False
        # But any other string value is valid:
        assert self.free_text_taxonomy.validate_value("Any text we want") is True

    def test_validate_value_closed(self):
        """
        Test validate_value() in a closed taxonomy
        """
        assert self.taxonomy.validate_value("Eukaryota") is True
        assert self.taxonomy.validate_value("Foobarensia") is False
        assert self.taxonomy.tag_for_value("Eukaryota").value == "Eukaryota"
        with pytest.raises(api.TagDoesNotExist):
            self.taxonomy.tag_for_value("Foobarensia")

    def test_clean_tag_in_taxonomy(self):
        # ObjectTags in a closed taxonomy require a tag in that taxonomy
        object_tag = ObjectTag(taxonomy=self.taxonomy, tag=Tag.objects.create(
            taxonomy=self.read_only_taxonomy,  # Different taxonomy
            value="PT",
        ))
        with pytest.raises(ValidationError):
            object_tag.full_clean()
        object_tag.tag = self.tag
        object_tag._value = self.tag.value  # pylint: disable=protected-access
        object_tag.full_clean()

    def test_clean_invalid_value(self):
        object_tag = ObjectTag(taxonomy=self.taxonomy, _value="")
        with self.assertRaises(ValidationError) as exc:
            object_tag.full_clean()
            assert exc.exception
            assert "Invalid _value - empty string" in str(exc.exception)

        for reserved_char in RESERVED_TAG_CHARS:
            object_tag = ObjectTag(taxonomy=self.taxonomy, _value=f"tag 1 {reserved_char} tag 2")
            with self.assertRaises(ValidationError) as exc:
                object_tag.full_clean()
                assert exc.exception
                assert f"Invalid _value - '{reserved_char}' it's not allowed" in str(exc.exception)

        object_tag = ObjectTag(taxonomy=self.taxonomy, _value="tag 1")
        object_tag.full_clean()

    def test_tag_case(self) -> None:
        """
        Test that the object_id is case sensitive.
        """
        # Tag with object_id with lower case
        api.tag_object("case:id:2", self.taxonomy, [self.chordata.value])

        # Tag with object_id with upper case should not trigger IntegrityError
        api.tag_object("CASE:id:2", self.taxonomy, [self.chordata.value])

        # Create another ObjectTag with lower case object_id should trigger IntegrityError
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                ObjectTag(
                    object_id="case:id:2",
                    taxonomy=self.taxonomy,
                    tag=self.chordata,
                ).save()

        # Create another ObjectTag with upper case object_id should trigger IntegrityError
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                ObjectTag(
                    object_id="CASE:id:2",
                    taxonomy=self.taxonomy,
                    tag=self.chordata,
                ).save()

    def test_invalid_id(self):
        """
        Test attempting to create object tags with invalid characters in the object ID
        """
        args = {"tags": ["test"], "taxonomy": self.free_text_taxonomy}
        with pytest.raises(ValidationError):
            api.tag_object(object_id="wildcard*", **args)
        with pytest.raises(ValidationError):
            api.tag_object(object_id="one,two,three", **args)
        api.tag_object(object_id="valid", **args)

    def test_is_deleted(self):
        self.taxonomy.allow_multiple = True
        self.taxonomy.save()

        object_id = "obj1"
        # Create some tags:
        api.tag_object(object_id, self.taxonomy, [self.archaea.value, self.bacteria.value])  # Regular tags
        api.tag_object(object_id, self.free_text_taxonomy, ["foo", "bar", "tribble"])  # Free text tags

        # At first, none of these will be deleted:
        assert [(t.value, t.is_deleted) for t in api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", False),
            ("foo", False),
            ("tribble", False),
            (self.archaea.value, False),
            (self.bacteria.value, False),
        ]

        # Delete "bacteria" from the taxonomy:
        api.delete_tags_from_taxonomy(self.taxonomy, ["Bacteria"], with_subtags=True)

        assert [(t.value, t.is_deleted) for t in api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", False),
            ("foo", False),
            ("tribble", False),
            (self.archaea.value, False),
            (self.bacteria.value, True),  # <--- deleted! But the value is preserved.
        ]

        # Then delete the whole free text taxonomy.
        # Patch explicit-delete detection because this
        # test validates ObjectTag deleted-state behavior, not tag deletion event-origins.
        with patch("openedx_tagging.signal_handlers._is_explicit_tag_delete", return_value=False):
            self.free_text_taxonomy.delete()

        assert [(t.value, t.is_deleted) for t in api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", True),  # <--- Deleted, but the value is preserved
            ("foo", True),  # <--- Deleted, but the value is preserved
            ("tribble", True),  # <--- Deleted, but the value is preserved
            (self.archaea.value, False),
            (self.bacteria.value, True),  # <--- deleted! But the value is preserved.
        ]


class TestTagLineage(TestCase):
    """
    Test the Tag.lineage field, which stores the full tab-separated ancestor
    path including the tag itself: "Root\tParent\t...\tThisValue\t".

    The tree used throughout this class:

        Charlie                  (depth 0)
            Alice                (depth 1)
                Delta            (depth 2)
                    Echo         (depth 3)
                        Foxtrot  (depth 4)
            Bob                  (depth 1)
        Danielle                 (depth 0)
    """

    def setUp(self):
        taxonomy = api.create_taxonomy("Test TagLineage")
        self.charlie = Tag.objects.create(taxonomy=taxonomy, value="Charlie")
        self.alice = Tag.objects.create(taxonomy=taxonomy, value="Alice", parent=self.charlie)
        self.bob = Tag.objects.create(taxonomy=taxonomy, value="Bob", parent=self.charlie)
        self.delta = Tag.objects.create(taxonomy=taxonomy, value="Delta", parent=self.alice)
        self.echo = Tag.objects.create(taxonomy=taxonomy, value="Echo", parent=self.delta)
        self.foxtrot = Tag.objects.create(taxonomy=taxonomy, value="Foxtrot", parent=self.echo)
        self.danielle = Tag.objects.create(taxonomy=taxonomy, value="Danielle")

    def test_root_tag(self):
        assert self.charlie.lineage == "Charlie\t"

    def test_depth_1(self):
        assert self.alice.lineage == "Charlie\tAlice\t"

    def test_depth_2(self):
        assert self.delta.lineage == "Charlie\tAlice\tDelta\t"

    def test_depth_3(self):
        assert self.echo.lineage == "Charlie\tAlice\tDelta\tEcho\t"

    def test_depth_4(self):
        assert self.foxtrot.lineage == "Charlie\tAlice\tDelta\tEcho\tFoxtrot\t"

    def test_second_root(self):
        assert self.danielle.lineage == "Danielle\t"

    def test_tree_sort_order(self):
        """
        Tags ordered by lineage come out in depth-first tree order:
        each parent immediately before its subtree, siblings alphabetically.
        Because lineage uses a case-insensitive collation, the sort matches
        what the old LOWER(sort_key) CTE produced.
        """
        tags = Tag.objects.filter(
            pk__in=[
                self.charlie.pk,
                self.alice.pk,
                self.bob.pk,
                self.delta.pk,
                self.echo.pk,
                self.foxtrot.pk,
                self.danielle.pk,
            ]
        ).order_by("lineage")
        # fmt: off
        assert [t.value for t in tags] == [
            "Charlie",   # Charlie\t
            "Alice",     # Charlie\tAlice\t
            "Delta",     # Charlie\tAlice\tDelta\t
            "Echo",      # Charlie\tAlice\tDelta\tEcho\t
            "Foxtrot",   # Charlie\tAlice\tDelta\tEcho\tFoxtrot\t
            "Bob",       # Charlie\tBob\t  (after Alice's entire subtree)
            "Danielle",  # Danielle\t
        ]
        # fmt: on

    def _refresh_all(self):
        """Refresh all tags from the database."""
        self.charlie.refresh_from_db()
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.delta.refresh_from_db()
        self.echo.refresh_from_db()
        self.foxtrot.refresh_from_db()
        self.danielle.refresh_from_db()

    def test_reparent_to_lower_depth(self):
        """
        Moving a tag to a deeper location updates its depth and lineage,
        and cascades to all its descendants.

        Before: Charlie -> Alice -> Delta -> Echo -> Foxtrot
        After:  Charlie -> Bob -> Alice -> Delta -> Echo -> Foxtrot
                (Alice moves from depth 1 to depth 2, all descendants shift +1)
        """
        self.alice.parent = self.bob
        self.alice.save()
        self._refresh_all()

        assert self.alice.depth == 2
        assert self.alice.lineage == "Charlie\tBob\tAlice\t"

        assert self.delta.depth == 3
        assert self.delta.lineage == "Charlie\tBob\tAlice\tDelta\t"

        assert self.echo.depth == 4
        assert self.echo.lineage == "Charlie\tBob\tAlice\tDelta\tEcho\t"

        assert self.foxtrot.depth == 5
        assert self.foxtrot.lineage == "Charlie\tBob\tAlice\tDelta\tEcho\tFoxtrot\t"

        # Bob's depth should be unchanged
        assert self.bob.depth == 1
        assert self.bob.lineage == "Charlie\tBob\t"

    def test_reparent_to_higher_depth(self):
        """
        Moving a tag to a shallower location updates its depth and lineage,
        and cascades to all its descendants.

        Before: Charlie -> Alice -> Delta -> Echo -> Foxtrot
        After:  Charlie -> Delta -> Echo -> Foxtrot
                (Delta moves from depth 2 to depth 1, all descendants shift -1)
        """
        self.delta.parent = self.charlie
        self.delta.save()
        self._refresh_all()

        assert self.delta.depth == 1
        assert self.delta.lineage == "Charlie\tDelta\t"

        assert self.echo.depth == 2
        assert self.echo.lineage == "Charlie\tDelta\tEcho\t"

        assert self.foxtrot.depth == 3
        assert self.foxtrot.lineage == "Charlie\tDelta\tEcho\tFoxtrot\t"

        # Alice should be unaffected
        assert self.alice.depth == 1
        assert self.alice.lineage == "Charlie\tAlice\t"

    def test_reparent_to_equal_depth(self):
        """
        Moving a tag (Delta) to a different parent at the same depth updates its
        lineage but leaves depths unchanged.

        Before: Charlie -> Alice -> Delta -> Echo -> Foxtrot
                Charlie -> Bob

        After:  Charlie -> Alice
                Charlie -> Bob -> Delta -> Echo -> Foxtrot

                (Delta moves from Alice to Bob, same depth 2)
        """
        self.delta.parent = self.bob
        self.delta.save()
        self._refresh_all()

        assert self.delta.depth == 2
        assert self.delta.lineage == "Charlie\tBob\tDelta\t"

        assert self.echo.depth == 3
        assert self.echo.lineage == "Charlie\tBob\tDelta\tEcho\t"

        assert self.foxtrot.depth == 4
        assert self.foxtrot.lineage == "Charlie\tBob\tDelta\tEcho\tFoxtrot\t"

        # Alice should be unaffected
        assert self.alice.depth == 1
        assert self.alice.lineage == "Charlie\tAlice\t"

    def test_reparent_to_different_root(self):
        """
        Moving a tag (Alice) to a parent under a completely different root
        updates the full lineage prefix for the tag and all its descendants.

        Before: Charlie -> Alice -> Delta -> Echo -> Foxtrot
                Danielle
        After:  Danielle -> Alice -> Delta -> Echo -> Foxtrot
                Charlie
        """
        self.alice.parent = self.danielle
        self.alice.save()
        self._refresh_all()

        assert self.alice.depth == 1
        assert self.alice.lineage == "Danielle\tAlice\t"

        assert self.delta.depth == 2
        assert self.delta.lineage == "Danielle\tAlice\tDelta\t"

        assert self.echo.depth == 3
        assert self.echo.lineage == "Danielle\tAlice\tDelta\tEcho\t"

        assert self.foxtrot.depth == 4
        assert self.foxtrot.lineage == "Danielle\tAlice\tDelta\tEcho\tFoxtrot\t"

        # Charlie is now childless but unchanged
        assert self.charlie.depth == 0
        assert self.charlie.lineage == "Charlie\t"

    def test_reparent_to_root(self):
        """
        Moving a child tag (Alice) to the root (no parent) updates depth to 0
        and removes all ancestor prefixes from its lineage and those of its
        descendants.

        Before: Charlie -> Alice -> Delta -> Echo -> Foxtrot
        After:  Alice -> Delta -> Echo -> Foxtrot  (Alice becomes a root tag)
        """
        self.alice.parent = None
        self.alice.save()
        self._refresh_all()

        assert self.alice.depth == 0
        assert self.alice.lineage == "Alice\t"

        assert self.delta.depth == 1
        assert self.delta.lineage == "Alice\tDelta\t"

        assert self.echo.depth == 2
        assert self.echo.lineage == "Alice\tDelta\tEcho\t"

        assert self.foxtrot.depth == 3
        assert self.foxtrot.lineage == "Alice\tDelta\tEcho\tFoxtrot\t"

        # Charlie and Bob are unaffected
        assert self.charlie.depth == 0
        assert self.charlie.lineage == "Charlie\t"
        assert self.bob.depth == 1
        assert self.bob.lineage == "Charlie\tBob\t"

    def test_rename(self):
        """
        Renaming a tag updates its own lineage and cascades to all descendants.

        Before: Charlie -> Alice    -> Delta -> Echo -> Foxtrot
        After:  Charlie -> Alicia✨ -> Delta -> Echo -> Foxtrot
        """
        self.alice.value = "Alicia"
        self.alice.save()
        self._refresh_all()

        assert self.alice.depth == 1
        assert self.alice.lineage == "Charlie\tAlicia\t"

        assert self.delta.depth == 2
        assert self.delta.lineage == "Charlie\tAlicia\tDelta\t"

        assert self.echo.depth == 3
        assert self.echo.lineage == "Charlie\tAlicia\tDelta\tEcho\t"

        assert self.foxtrot.depth == 4
        assert self.foxtrot.lineage == "Charlie\tAlicia\tDelta\tEcho\tFoxtrot\t"

        # Unrelated tags are unaffected
        assert self.charlie.depth == 0
        assert self.charlie.lineage == "Charlie\t"
        assert self.bob.depth == 1
        assert self.bob.lineage == "Charlie\tBob\t"

    # TODO: The following event-emission tests don't really belong in TestTagLineage.
    # They should be moved to a separate test_events.py module.
    @patch("openedx_tagging.signal_handlers.emit_content_object_associations_changed_for_tag_task.delay")
    def test_rename_updates_search_index(self, mock_task_delay) -> None:
        """
        Renaming a tag should enqueue an async task that emits
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED events.
        """
        api.tag_object(
            object_id="content-v1:org+course+run+type@unit+block@123",
            taxonomy=self.alice.taxonomy,
            tags=[self.alice.value],
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.alice.value = "Alicia"
            self.alice.save()

        assert mock_task_delay.call_count == 1
        assert mock_task_delay.call_args[1]['tag_id'] == self.alice.id

    @patch("openedx_tagging.signal_handlers.emit_content_object_associations_changed_for_object_ids_task.delay")
    def test_delete_updates_search_index(self, mock_task_delay) -> None:
        """
        Deleting a tag should enqueue an async task that emits
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED events for affected objects.

        Note: this tests deleting a ``Tag`` (not an ``ObjectTag``). Deleting a
        ``Tag`` triggers the event here in openedx-learning. Deleting an
        ``ObjectTag`` (i.e. untagging a content object) triggers the same event
        in openedx-platform instead, so that case is not tested here.
        """
        object_id = "content-v1:org+course+run+type@unit+block@125"
        api.tag_object(
            object_id=object_id,
            taxonomy=self.bob.taxonomy,
            tags=[self.bob.value],
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.bob.delete()

        assert mock_task_delay.call_count == 1
        assert mock_task_delay.call_args[1]["object_ids"] == [object_id]

    @patch("openedx_tagging.signal_handlers.emit_content_object_associations_changed_for_object_ids_task.delay")
    def test_delete_with_descendants_updates_search_index(self, mock_task_delay) -> None:
        """
        Deleting a tag should also enqueue updates for any deleted descendants.
        """
        alice_object_id = "content-v1:org+course+run+type@unit+block@126"
        delta_object_id = "content-v1:org+course+run+type@unit+block@127"
        api.tag_object(
            object_id=alice_object_id,
            taxonomy=self.alice.taxonomy,
            tags=[self.alice.value],
        )
        api.tag_object(
            object_id=delta_object_id,
            taxonomy=self.delta.taxonomy,
            tags=[self.delta.value],
        )

        with self.captureOnCommitCallbacks(execute=True):
            api.delete_tags_from_taxonomy(self.alice.taxonomy, ["Alice"], with_subtags=True)

        assert mock_task_delay.call_count == 1
        assert set(mock_task_delay.call_args.kwargs["object_ids"]) == {
            alice_object_id,
            delta_object_id,
        }

    @patch("openedx_tagging.signal_handlers.emit_content_object_associations_changed_for_object_ids_task.delay")
    def test_taxonomy_delete_updates_search_index(self, mock_task_delay) -> None:
        """
        Deleting a Taxonomy should enqueue updates for any tagged objects,
        including ones tagged with deep descendants of root tags.
        """
        object_id = "content-v1:org+course+run+type@unit+block@128"
        api.tag_object(
            object_id=object_id,
            taxonomy=self.foxtrot.taxonomy,
            tags=[self.foxtrot.value],
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.foxtrot.taxonomy.delete()

        assert mock_task_delay.call_count == 1
        assert mock_task_delay.call_args.kwargs["object_ids"] == [object_id]

    @patch("openedx_tagging.tasks.CONTENT_OBJECT_ASSOCIATIONS_CHANGED", new_callable=MagicMock)
    def test_emit_content_object_associations_changed_for_object_ids_task(self, mock_signal) -> None:
        """Task emits one CONTENT_OBJECT_ASSOCIATIONS_CHANGED event per distinct object."""
        first_object_id = "content-v1:org+course+run+type@unit+block@123"
        second_object_id = "content-v1:org+course+run+type@unit+block@124"

        emitted_events = emit_content_object_associations_changed_for_object_ids_task(
            [first_object_id, second_object_id, first_object_id]
        )

        assert emitted_events == 2
        assert mock_signal.send_event.call_count == 2
        emitted_object_ids = {
            call.kwargs["content_object"].object_id
            for call in mock_signal.send_event.call_args_list
        }
        assert emitted_object_ids == {first_object_id, second_object_id}

    @patch("openedx_tagging.tasks.CONTENT_OBJECT_ASSOCIATIONS_CHANGED", new_callable=MagicMock)
    def test_emit_content_object_associations_changed_for_tag_task(self, mock_signal) -> None:
        """Task emits one CONTENT_OBJECT_ASSOCIATIONS_CHANGED event per associated object."""
        first_object_id = "content-v1:org+course+run+type@unit+block@123"
        second_object_id = "content-v1:org+course+run+type@unit+block@124"
        api.tag_object(
            object_id=first_object_id,
            taxonomy=self.alice.taxonomy,
            tags=[self.alice.value],
        )
        api.tag_object(
            object_id=second_object_id,
            taxonomy=self.alice.taxonomy,
            tags=[self.alice.value],
        )

        emitted_events = emit_content_object_associations_changed_for_tag_task(self.alice.id)

        assert emitted_events == 2
        assert mock_signal.send_event.call_count == 2
        emitted_object_ids = {
            call.kwargs["content_object"].object_id
            for call in mock_signal.send_event.call_args_list
        }
        assert emitted_object_ids == {first_object_id, second_object_id}


class TestTagExternalIdCandidate(TestCase):
    """
    Direct unit tests for the tag_external_id_candidate() helper.
    """

    def test_bare_value_round_trips(self) -> None:
        assert tag_external_id_candidate("Bacteria", 1) == "Bacteria"

    def test_long_value_truncates(self) -> None:
        value = "x" * (TAG_EXTERNAL_ID_MAX_LENGTH + 50)
        candidate = tag_external_id_candidate(value, 1)
        assert candidate == value[:TAG_EXTERNAL_ID_MAX_LENGTH]
        assert len(candidate) == TAG_EXTERNAL_ID_MAX_LENGTH

    def test_whitespace_at_truncation_boundary_is_stripped(self) -> None:
        # Construct a value where the character landing exactly at the truncation
        # boundary is whitespace, so the naive slice would leave a trailing space.
        value = ("a" * (TAG_EXTERNAL_ID_MAX_LENGTH - 1)) + " " + "bbbbb"
        candidate = tag_external_id_candidate(value, 1)
        assert candidate == "a" * (TAG_EXTERNAL_ID_MAX_LENGTH - 1)
        assert not candidate.endswith(" ")

    def test_different_attempts_differ(self) -> None:
        value = "Some Tag"
        assert tag_external_id_candidate(value, 1) != tag_external_id_candidate(value, 2)


class TestTagExternalIdGeneration(TestTagTaxonomyMixin, TestCase):
    """
    Tests for the three entry points that generate a Tag's external_id.
    """

    def test_tag_save_generates_external_id_from_value(self) -> None:
        tag = Tag(taxonomy=self.taxonomy, value="Generated From Save")
        tag.save()
        assert tag.external_id == "Generated From Save"

    def test_bare_tag_create_generates_external_id(self) -> None:
        # Proves the generation logic lives in Tag.save(), not just in Taxonomy.add_tag().
        tag = Tag.objects.create(taxonomy=self.taxonomy, value="Bare Create Tag")
        assert tag.external_id == "Bare Create Tag"

    def test_add_tag_generates_external_id(self) -> None:
        tag = self.taxonomy.add_tag("Added Via Taxonomy")
        assert tag.external_id == "Added Via Taxonomy"

    def test_add_tag_duplicate_external_id_raises(self) -> None:
        self.taxonomy.add_tag("First Tag", external_id="dup-id")
        with pytest.raises(ValueError):
            self.taxonomy.add_tag("Second Tag", external_id="dup-id")
        # Also raises on a case-different duplicate:
        with pytest.raises(ValueError):
            self.taxonomy.add_tag("Third Tag", external_id="DUP-ID")

    def test_rename_preserves_external_id(self) -> None:
        original_external_id = self.bacteria.external_id
        self.bacteria.value = "Renamed Bacteria"
        self.bacteria.save()
        assert self.bacteria.external_id == original_external_id

    def test_save_resolves_generated_external_id_collision(self) -> None:
        """
        Two tags whose values share the same first 255 characters would otherwise
        generate the same attempt-1 candidate; Tag.save()'s own collision branch (not
        the migration's separate copy of the same logic) must resolve that here.
        """
        value_one = "X" * (TAG_EXTERNAL_ID_MAX_LENGTH + 45)
        value_two = ("X" * TAG_EXTERNAL_ID_MAX_LENGTH) + ("Y" * 50)

        first = Tag.objects.create(taxonomy=self.taxonomy, value=value_one)
        second = Tag.objects.create(taxonomy=self.taxonomy, value=value_two)

        assert first.external_id == "X" * TAG_EXTERNAL_ID_MAX_LENGTH
        assert second.external_id != first.external_id
        assert second.external_id
        assert second.external_id == tag_external_id_candidate(value_two, 2)
        assert second.external_id.endswith("-2")

    def test_read_only_taxonomy_tags_get_external_id_and_stay_read_only(self) -> None:
        """
        A tag created directly on a read-only taxonomy (bypassing add_tag()'s own
        read_only check, e.g. as a plugin writing directly to the model might) still
        gets a generated external_id, and the taxonomy's read_only enforcement in
        add_tag()/update_tag()/delete_tags() is unaffected by that.
        """
        read_only_taxonomy = Taxonomy.objects.create(name="RO Test", read_only=True)
        tag = Tag.objects.create(taxonomy=read_only_taxonomy, value="RO Tag")
        assert tag.external_id == "RO Tag"

        with pytest.raises(ValueError):
            read_only_taxonomy.add_tag("Another Tag")
        with pytest.raises(ValueError):
            read_only_taxonomy.update_tag("RO Tag", "Renamed")
        with pytest.raises(ValueError):
            read_only_taxonomy.delete_tags(["RO Tag"])
