"""
Tests that archiving a locked ObjectTag, instead of deleting it, keeps the criteria that point at it.

These live here rather than in ``tests/openedx_tagging`` because they need a CompetencyCriterion, and the
tagging package's tests shouldn't depend on this one.
"""
import pytest

from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
)
from openedx_tagging import api as tagging_api
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db


def test_criterion_survives_remove_and_reapply(
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
) -> None:
    """
    AC: removing a locked association archives it, so its criterion isn't cascaded away; re-applying restores it.
    """
    tagging_api.lock_object_tags_for_deletion(object_tag.pk)
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    tagging_api.tag_object(object_tag.object_id, competency_taxonomy, [])

    archived = ObjectTag.objects.get(pk=object_tag.pk)
    assert archived.archived is True
    assert archived.deletion_locked is True
    assert CompetencyCriterion.objects.get(pk=criterion.pk).object_tag == archived

    tagging_api.tag_object(object_tag.object_id, competency_taxonomy, [tag.value])

    reapplied = ObjectTag.objects.get(object_id=object_tag.object_id, taxonomy=competency_taxonomy)
    assert reapplied.pk == object_tag.pk
    assert reapplied.archived is False
    assert CompetencyCriterion.objects.get(pk=criterion.pk).object_tag_id == object_tag.pk
