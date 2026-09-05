"""
Tests for the CompetencyTaxonomy model.
"""
import pytest

from openedx_learning.models import CompetencyTaxonomy
from openedx_tagging.models import Taxonomy

pytestmark = pytest.mark.django_db


@pytest.fixture(name="competency_taxonomy")
def _competency_taxonomy() -> CompetencyTaxonomy:
    """Create a CompetencyTaxonomy for use in these tests."""
    return CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")


def test_create_writes_both_rows(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    Creating a CompetencyTaxonomy writes both the parent Taxonomy row and the child row.
    """
    assert Taxonomy.objects.filter(pk=competency_taxonomy.pk).exists()
    assert CompetencyTaxonomy.objects.filter(pk=competency_taxonomy.pk).exists()


def test_mti_round_trip(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    The MTI relationship works in both directions: the child reads the parent's
    fields directly, and the parent reaches the child via the default accessor.
    """
    assert competency_taxonomy.name == "Nursing"
    parent = Taxonomy.objects.get(pk=competency_taxonomy.pk)
    assert parent.competencytaxonomy == competency_taxonomy


def test_plain_taxonomy_has_no_competencytaxonomy() -> None:
    """
    A plain Taxonomy (no CompetencyTaxonomy row) raises RelatedObjectDoesNotExist.
    """
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    # Django builds the accessor's RelatedObjectDoesNotExist as a subclass of the child
    # model's DoesNotExist, so catching that names no dynamically added attribute.
    with pytest.raises(CompetencyTaxonomy.DoesNotExist):
        _ = plain.competencytaxonomy


def test_taxonomy_overrides_org_defaults_false(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    taxonomy_overrides_org defaults to False, so an organization-scoped profile wins the
    contested case by default until an author opts a taxonomy out. See ADR-0002 Decision 1.
    """
    assert competency_taxonomy.taxonomy_overrides_org is False


def test_delete_cascades_both_directions() -> None:
    """
    Deleting the parent Taxonomy removes the CompetencyTaxonomy row, and deleting
    the child removes the parent row too.
    """
    ct1 = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    Taxonomy.objects.get(pk=ct1.pk).delete()
    assert not CompetencyTaxonomy.objects.filter(pk=ct1.pk).exists()

    ct2 = CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1")
    ct2.delete()
    assert not Taxonomy.objects.filter(pk=ct2.pk).exists()
