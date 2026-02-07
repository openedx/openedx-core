"""
Test the tagging base models
"""
from __future__ import annotations

import ddt  # type: ignore[import]
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test.testcases import TestCase

from openedx_tagging import api
from openedx_tagging.models import LanguageTaxonomy, ObjectTag, Tag, Taxonomy
from openedx_tagging.models.utils import RESERVED_TAG_CHARS

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
        self.system_taxonomy = Taxonomy.objects.get(name="System defined taxonomy")
        self.language_taxonomy = LanguageTaxonomy.objects.get(name="Languages")
        self.user_taxonomy = Taxonomy.objects.get(name="User Authors").cast()
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
        self.system_taxonomy_tag = get_tag("System Tag 1")
        self.english_tag = self.language_taxonomy.tag_for_external_id("en")
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


class TaxonomyTestSubclassA(Taxonomy):
    """
    Model A for testing the taxonomy subclass casting.
    """

    class Meta:
        managed = False
        proxy = True
        app_label = "oel_tagging"


class TaxonomyTestSubclassB(TaxonomyTestSubclassA):
    """
    Model B for testing the taxonomy subclass casting.
    """

    class Meta:
        managed = False
        proxy = True
        app_label = "oel_tagging"


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

    def test_system_defined(self):
        assert not self.taxonomy.system_defined
        assert self.system_taxonomy.cast().system_defined

    def test_representations(self):
        assert (
            str(self.taxonomy) == repr(self.taxonomy) == "<Taxonomy> (1) Life on Earth"
        )
        assert (
            str(self.language_taxonomy)
            == repr(self.language_taxonomy)
            == "<LanguageTaxonomy> (-1) Languages"
        )
        assert str(self.bacteria) == repr(self.bacteria) == "<Tag> (1) Bacteria"

    def test_taxonomy_cast(self):
        for subclass in (
            TaxonomyTestSubclassA,
            # Ensure that casting to a sub-subclass works as expected
            TaxonomyTestSubclassB,
            # and that we can un-set the subclass
            None,
        ):
            self.taxonomy.taxonomy_class = subclass
            cast_taxonomy = self.taxonomy.cast()
            if subclass:
                expected_class = subclass.__name__
            else:
                expected_class = "Taxonomy"
                assert self.taxonomy == cast_taxonomy
            assert (
                str(cast_taxonomy)
                == repr(cast_taxonomy)
                == f"<{expected_class}> (1) Life on Earth"
            )

    def test_taxonomy_cast_import_error(self):
        taxonomy = Taxonomy.objects.create(
            name="Invalid cast", export_id='invalid_cast', _taxonomy_class="not.a.class"
        )
        # Error is logged, but ignored.
        cast_taxonomy = taxonomy.cast()
        assert cast_taxonomy == taxonomy
        assert (
            str(cast_taxonomy)
            == repr(cast_taxonomy)
            == f"<Taxonomy> ({taxonomy.id}) Invalid cast"
        )

    def test_taxonomy_cast_bad_value(self):
        with self.assertRaises(ValueError) as exc:
            self.taxonomy.taxonomy_class = str
        assert "<class 'str'> must be a subclass of Taxonomy" in str(exc.exception)

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
        get_filtered_tags(). Without counts included.
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, include_counts=False))
        common_fields = {"depth": 0, "parent_value": None, "external_id": None}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the root tags, in alphabetical order:
            {"value": "Archaea", "child_count": 3, "descendant_count": 3, **common_fields},
            {"value": "Bacteria", "child_count": 2, "descendant_count": 2, **common_fields},
            {"value": "Eukaryota", "child_count": 5, "descendant_count": 13, **common_fields},
        ]

    def test_get_child_tags_one_level(self) -> None:
        """
        Test basic retrieval of tags one level below the "Eukaryota" root tag in
        the closed taxonomy, using get_filtered_tags(). With counts included.
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, parent_tag_value="Eukaryota", include_counts=True))
        common_fields = {"depth": 1, "parent_value": "Eukaryota", "usage_count": 0, "external_id": None}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the Eukaryota tags, in alphabetical order:
            {"value": "Animalia", "child_count": 7, "descendant_count": 8, **common_fields},
            {"value": "Fungi", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Monera", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Plantae", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Protista", "child_count": 0, "descendant_count": 0, **common_fields},
        ]

    def test_get_grandchild_tags_one_level(self) -> None:
        """
        Test basic retrieval of a single level of tags at two level belows the
        "Eukaryota" root tag in the closed taxonomy, using get_filtered_tags().
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, parent_tag_value="Animalia"))
        common_fields = {"depth": 2, "parent_value": "Animalia", "external_id": None}
        for r in result:
            del r["_id"]  # Remove the internal database IDs; they aren't interesting here and a other tests check them
        assert result == [
            # These are the Eukaryota tags, in alphabetical order:
            {"value": "Arthropoda", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Chordata", "child_count": 1, "descendant_count": 1, **common_fields},
            {"value": "Cnidaria", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Ctenophora", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Gastrotrich", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Placozoa", "child_count": 0, "descendant_count": 0, **common_fields},
            {"value": "Porifera", "child_count": 0, "descendant_count": 0, **common_fields},
        ]

    def test_get_depth_1_search_term(self) -> None:
        """
        Filter the root tags to only those that match a search term
        """
        result = list(self.taxonomy.get_filtered_tags(depth=1, search_term="ARCH", include_counts=True))
        assert result == [
            {
                "value": "Archaea",
                "child_count": 3,
                "descendant_count": 3,
                "depth": 0,
                "usage_count": 0,
                "parent_value": None,
                "external_id": None,
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
                "descendant_count": 0,
                "depth": 1,
                "parent_value": "Bacteria",
                "external_id": None,
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
        # When listing the tags below a specific tag, there is one additional query to load each ancestor tag:
        with self.assertNumQueries(2):
            self.test_get_child_tags_one_level()
        with self.assertNumQueries(2):
            self.test_get_depth_1_child_search_term()
        with self.assertNumQueries(3):
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
            "Eukaryota (None) (children: 5 + 8)",
            "  Animalia (Eukaryota) (children: 7 + 1)",
            "    Arthropoda (Animalia) (children: 0)",
            "    Chordata (Animalia) (children: 1)",  # note this has a child but the child is not included
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
            "Eukaryota (None) (children: 1 + 1)",  # Has one child that matches, plus one additional matching descendant
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
            "Eukaryota (None) (children: 2 + 2)",  # 2 direct matching children, 2 additional matching descendants
            "  Animalia (Eukaryota) (children: 2)",
            "    Arthropoda (Animalia) (children: 0)",  # match
            "    Gastrotrich (Animalia) (children: 0)",  # match
            "  Protista (Eukaryota) (children: 0)",  # match
        ]

    def test_tags_deep(self) -> None:
        """
        Test getting a deep tag in the taxonomy
        """
        result = list(self.taxonomy.get_filtered_tags(parent_tag_value="Chordata", include_counts=True))
        assert result == [
            {
                "value": "Mammalia",
                "parent_value": "Chordata",
                "depth": 3,
                "usage_count": 0,
                "child_count": 0,
                "descendant_count": 0,
                "external_id": None,
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

    def test_usage_count(self) -> None:
        """
        Test that the usage count in the results is right
        """
        api.tag_object(object_id="obj01", taxonomy=self.taxonomy, tags=["Bacteria"])
        api.tag_object(object_id="obj02", taxonomy=self.taxonomy, tags=["Bacteria"])
        api.tag_object(object_id="obj03", taxonomy=self.taxonomy, tags=["Bacteria"])
        api.tag_object(object_id="obj04", taxonomy=self.taxonomy, tags=["Eubacteria"])
        # Now the API should reflect these usage counts:
        result = pretty_format_tags(self.taxonomy.get_filtered_tags(search_term="bacteria", include_counts=True))
        assert result == [
            "Bacteria (None) (used: 3, children: 2)",
            "  Archaebacteria (Bacteria) (used: 0, children: 0)",
            "  Eubacteria (Bacteria) (used: 1, children: 0)",
        ]
        # Same with depth=1, which uses a different query internally:
        result1 = pretty_format_tags(
            self.taxonomy.get_filtered_tags(search_term="bacteria", include_counts=True, depth=1)
        )
        assert result1 == [
            "Bacteria (None) (used: 3, children: 2)",
        ]

    def test_tree_sort(self) -> None:
        """
        Verify that taxonomies can be sorted correctly in tree orer (case insensitive).

        The taxonomy used contains values that are tricky to sort correctly unless the tree sort algorithm is correct.
        """
        taxonomy = self.create_sort_test_taxonomy()
        result = pretty_format_tags(taxonomy.get_filtered_tags())
        assert result == [
            "1 (None) (children: 4 + 1)",
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

    def test_descendant_counts(self) -> None:
        """
        Test getting the descendant count on a taxonomy known to cause aggregation
        bugs unless the aggregations are correctly specified with distinct=True

        https://docs.djangoproject.com/en/5.0/topics/db/aggregation/#combining-multiple-aggregations
        """
        taxonomy = api.create_taxonomy("ESDC Subset")
        api.add_tag_to_taxonomy(taxonomy, "Interests")  # root tag
        api.add_tag_to_taxonomy(taxonomy, "Holland Codes", parent_tag_value="Interests")  # child tag
        # Create the grandchild tag:
        g_tag = api.add_tag_to_taxonomy(taxonomy, "Interests - Holland Codes", parent_tag_value="Holland Codes")
        # Create the 6 great-grandchild tags:
        api.add_tag_to_taxonomy(taxonomy, "Artistic", parent_tag_value=g_tag.value)
        api.add_tag_to_taxonomy(taxonomy, "Conventional", parent_tag_value=g_tag.value)
        api.add_tag_to_taxonomy(taxonomy, "Enterprising", parent_tag_value=g_tag.value)
        api.add_tag_to_taxonomy(taxonomy, "Investigative", parent_tag_value=g_tag.value)
        api.add_tag_to_taxonomy(taxonomy, "Realistic", parent_tag_value=g_tag.value)
        api.add_tag_to_taxonomy(taxonomy, "Social", parent_tag_value=g_tag.value)

        result = pretty_format_tags(taxonomy.get_filtered_tags(depth=1, include_counts=True))
        assert result == [
            "Interests (None) (used: 0, children: 1 + 7)",  # 1 child + (1 grandchild and 6 great grandchild tags)
        ]
        result2 = pretty_format_tags(taxonomy.get_filtered_tags(depth=None, include_counts=True))
        assert result2 == [
            "Interests (None) (used: 0, children: 1 + 7)",
            "  Holland Codes (Interests) (used: 0, children: 1 + 6)",
            "    Interests - Holland Codes (Holland Codes) (used: 0, children: 6)",
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
        Without counts included.
        """
        result = list(self.taxonomy.get_filtered_tags(include_counts=False))
        common_fields = {"child_count": 0, "depth": 0, "parent_value": None, "external_id": None, "_id": None}
        assert result == [
            # These should appear in alphabetical order:
            {"value": "double", **common_fields},
            {"value": "solo", **common_fields},
            {"value": "triple", **common_fields},
        ]

    def test_get_filtered_tags_with_count(self):
        """
        Test basic retrieval of all tags in the taxonomy.
        Without counts included.
        """
        result = list(self.taxonomy.get_filtered_tags(include_counts=True))
        common_fields = {"child_count": 0, "depth": 0, "parent_value": None, "external_id": None, "_id": None}
        assert result == [
            # These should appear in alphabetical order:
            {"value": "double", "usage_count": 2, **common_fields},
            {"value": "solo", "usage_count": 1, **common_fields},
            {"value": "triple", "usage_count": 3, **common_fields},
        ]

    def test_get_filtered_tags_num_queries(self):
        """
        Test that the number of queries used by get_filtered_tags() is fixed
        and not O(n) or worse.
        """
        with self.assertNumQueries(1):
            self.test_get_filtered_tags()
        with self.assertNumQueries(1):
            self.test_get_filtered_tags_with_count()

    def test_get_filtered_tags_with_search(self) -> None:
        """
        Test basic retrieval of only matching tags.
        """
        result1 = list(self.taxonomy.get_filtered_tags(search_term="le", include_counts=True))
        common_fields = {"child_count": 0, "depth": 0, "parent_value": None, "external_id": None, "_id": None}
        assert result1 == [
            # These should appear in alphabetical order:
            {"value": "double", "usage_count": 2, **common_fields},
            {"value": "triple", "usage_count": 3, **common_fields},
        ]
        # And it should be case insensitive:
        result2 = list(self.taxonomy.get_filtered_tags(search_term="LE", include_counts=True))
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

        # But if the taxonomy is deleted, then the object_tag's export_id reverts to our cached export_id
        self.taxonomy.delete()
        self.object_tag.refresh_from_db()
        assert self.object_tag.export_id == "another-taxonomy"

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
            taxonomy=self.system_taxonomy,  # Different taxonomy
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

        # Then delete the whole free text taxonomy
        self.free_text_taxonomy.delete()

        assert [(t.value, t.is_deleted) for t in api.get_object_tags(object_id, include_deleted=True)] == [
            ("bar", True),  # <--- Deleted, but the value is preserved
            ("foo", True),  # <--- Deleted, but the value is preserved
            ("tribble", True),  # <--- Deleted, but the value is preserved
            (self.archaea.value, False),
            (self.bacteria.value, True),  # <--- deleted! But the value is preserved.
        ]
