"""
Tests for the CBE public API surface (openedx_learning.api).
"""
import pytest

from openedx_learning.api import create_competency_taxonomy, is_competency_taxonomy, select_competency_taxonomies
from openedx_learning.models import CompetencyTaxonomy
from openedx_tagging.models import Taxonomy

pytestmark = pytest.mark.django_db


def test_create_competency_taxonomy_saves_both_rows() -> None:
    """
    create_competency_taxonomy() saves a CompetencyTaxonomy and Taxonomy row that both
    carry the given field values.

    Re-fetching from Taxonomy.objects (not just CompetencyTaxonomy.objects) is the
    regression check for using save_base(raw=True): a plain save() on the child
    instance would re-save the parent Taxonomy row with blank/default field values,
    which this assertion would catch.
    """
    result = create_competency_taxonomy(
        name="Nursing",
        description="Nursing competencies",
        enabled=False,
        allow_multiple=False,
        allow_free_text=True,
        read_only=True,
        export_id="nursing-v1",
    )

    assert isinstance(result, CompetencyTaxonomy)
    assert is_competency_taxonomy(result) is True

    for taxonomy in (
        CompetencyTaxonomy.objects.get(pk=result.pk),
        Taxonomy.objects.get(pk=result.pk),
    ):
        assert taxonomy.name == "Nursing"
        assert taxonomy.description == "Nursing competencies"
        assert taxonomy.enabled is False
        assert taxonomy.allow_multiple is False
        assert taxonomy.allow_free_text is True
        assert taxonomy.read_only is True
        assert taxonomy.export_id == "nursing-v1"


def test_create_competency_taxonomy_defaults() -> None:
    """
    create_competency_taxonomy() applies the same defaults as create_taxonomy() when
    only name is given, including an auto-generated export_id.
    """
    result = create_competency_taxonomy(name="Welding")

    assert result.enabled is True
    assert result.allow_multiple is True
    assert result.allow_free_text is False
    assert result.read_only is False
    assert result.export_id


def test_is_competency_taxonomy() -> None:
    """
    is_competency_taxonomy() is True for a competency taxonomy, False for a plain one.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")

    assert is_competency_taxonomy(Taxonomy.objects.get(pk=competency.pk)) is True
    assert is_competency_taxonomy(plain) is False


def test_is_competency_taxonomy_on_child_instance_directly() -> None:
    """
    is_competency_taxonomy() also returns True when handed a CompetencyTaxonomy
    instance directly, not just a parent Taxonomy fetched from the DB.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    assert is_competency_taxonomy(competency) is True


def test_is_competency_taxonomy_on_unsaved_instance() -> None:
    """
    is_competency_taxonomy() returns False for an unsaved Taxonomy, rather than raising.
    """
    # pk is None, so the reverse one-to-one descriptor short-circuits and raises
    # RelatedObjectDoesNotExist, which Django defines as an AttributeError subclass
    # precisely so hasattr() catches it here instead of propagating.
    unsaved = Taxonomy(name="Unsaved", export_id="unsaved-v1")
    assert is_competency_taxonomy(unsaved) is False


def test_select_competency_taxonomies_avoids_n_plus_1(django_assert_num_queries) -> None:
    """
    select_competency_taxonomies() joins the CompetencyTaxonomy row in, so checking
    is_competency_taxonomy() on every row in the queryset costs one query, not N+1.
    """
    competency1 = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    competency2 = CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    # Scoped to just these three: unfiltered Taxonomy.objects.all() would also pick up
    # any taxonomies seeded outside this test, which would make the True/False counts
    # below depend on incidental fixture data.
    taxonomies = Taxonomy.objects.filter(pk__in=[competency1.pk, competency2.pk, plain.pk])

    with django_assert_num_queries(1):
        results = [is_competency_taxonomy(t) for t in select_competency_taxonomies(taxonomies)]

    assert results.count(True) == 2
    assert results.count(False) == 1
