"""Shared test helpers for the CBE test modules."""
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.applets.cbe.api import _create_leaf_group
from openedx_learning.models import CompetencyCriterion, CompetencyRuleProfile, CompetencyTaxonomy
from openedx_tagging.models import ObjectTag, Tag


def usage_key(course_run: CourseRun, block_id: str) -> str:
    """Build a gradeable-subsection-shaped usage key string under `course_run`."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@{block_id}"


def make_leaf_criterion(
    tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile, block_id: str,
) -> CompetencyCriterion:
    """Create a leaf group and one live CompetencyCriterion for `tag` in `course_run`."""
    leaf = _create_leaf_group(tag, course_run)
    object_tag = ObjectTag.objects.create(object_id=usage_key(course_run, block_id), taxonomy=tag.taxonomy, tag=tag)
    return CompetencyCriterion.objects.create(group=leaf, object_tag=object_tag, rule_profile=default_rule_profile)


def make_course_run(organization: Organization, course_code: str, run_code: str) -> CourseRun:
    """Create a CourseRun distinct from the `course_run` fixture, for the different-course tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code=course_code)
    return CourseRun.objects.create(catalog_course=catalog_course, run_code=run_code)


def make_tag(taxonomy: CompetencyTaxonomy, value: str, parent: Tag | None = None) -> Tag:
    """Create a Tag on `taxonomy`, optionally under `parent`, for taxonomy-hierarchy tests."""
    return Tag.objects.create(taxonomy=taxonomy, value=value, parent=parent)


def make_skill_family(taxonomy: CompetencyTaxonomy) -> tuple[Tag, Tag, Tag, Tag]:
    """
    Create "Cognitive Skills" > "Critical Thinking" > "Inference", plus "Analysis" as a sibling of "Inference".
    """
    cognitive_skills = Tag.objects.create(taxonomy=taxonomy, value="Cognitive Skills")
    critical_thinking = Tag.objects.create(taxonomy=taxonomy, value="Critical Thinking", parent=cognitive_skills)
    inference = Tag.objects.create(taxonomy=taxonomy, value="Inference", parent=critical_thinking)
    analysis = Tag.objects.create(taxonomy=taxonomy, value="Analysis", parent=critical_thinking)
    return cognitive_skills, critical_thinking, inference, analysis
