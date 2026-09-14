"""
Tests for CompetencyCriteriaGroup, the internal AND/OR node of a CompetencyAchievementCriteria
tree.

Each test name states the behavior it pins. Reading top to bottom gives the model's contract:
its columns, its tree shape, the two constraints ADR-0002 Decision 2 deliberately leaves out,
then its indexes and history.

Delete behavior is not covered here. Nothing in this module deletes a row that another row
points at. See test_criteria_group_deletion.py, in this same change, for this model's own
`on_delete` values and the tests that exercise them.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.db import connection, models

from openedx_catalog.models import CourseRun
from openedx_learning.models import CompetencyCriteriaGroup, CompetencyTaxonomy, LogicOperator
from openedx_tagging.models import Tag, Taxonomy

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------------------------
# Schema


# ---------------------------------------------------------------------------------------------


def test_group_has_exactly_the_columns_adr_0002_decision_2_lists() -> None:
    """
    CompetencyCriteriaGroup's columns are exactly the ones ADR-0002 Decision 2 lists, with
    `parent`, `course`, and `logic_operator` optional and the rest required. `tag` keeps the
    legacy `oel_tagging_tag_id` column name. No `archived` column yet; that arrives with #642.
    """
    fields = [f for f in CompetencyCriteriaGroup._meta.get_fields() if f.concrete]
    assert {f.name for f in fields} == {
        "id", "uuid", "parent", "tag", "course", "name", "ordering", "logic_operator",
    }
    assert {f.name for f in fields if f.null} == {"parent", "course", "logic_operator"}
    assert CompetencyCriteriaGroup._meta.get_field("parent").remote_field.model is CompetencyCriteriaGroup
    assert CompetencyCriteriaGroup._meta.get_field("tag").remote_field.model is Tag
    assert CompetencyCriteriaGroup._meta.get_field("tag").db_column == "oel_tagging_tag_id"
    assert CompetencyCriteriaGroup._meta.get_field("course").remote_field.model is CourseRun


# ---------------------------------------------------------------------------------------------
# Tree shape, and the two constraints ADR-0002 Decision 2 deliberately leaves out


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "logic_operator",
    [
        pytest.param(LogicOperator.AND, id="and"),
        pytest.param(LogicOperator.OR, id="or"),
        pytest.param(None, id="null"),
    ],
)
def test_group_logic_operator_accepts_and_or_and_null_regardless_of_child_count(
    logic_operator: str | None, tag: Tag
) -> None:
    """
    logic_operator accepts AND, OR, or null. Nothing at the data layer constrains it by how many
    children the group actually has: a group with zero children and a group with two children both
    save successfully with any of the three values. See ADR-0002 Decision 2; the database cannot
    see a group's future children at save time (a child's parent FK cannot point at a row that
    doesn't have a primary key yet), so this is enforced nowhere at this layer, deliberately.
    """
    childless = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
    assert childless.pk is not None

    parent = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=logic_operator)
    CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
    CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent)
    assert CompetencyCriteriaGroup.objects.filter(parent=parent).count() == 2


def test_a_root_group_has_a_null_parent_and_a_child_points_at_the_group_it_was_created_under(tag: Tag) -> None:
    """
    A CompetencyCriteriaGroup's parent is null for a root and points at its parent for a child.
    See ADR-0002 Decision 2.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag, logic_operator=None)
    assert root.parent is None

    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root, logic_operator=LogicOperator.AND)
    assert child.parent == root


def test_group_has_no_unique_constraint_on_parent_and_ordering(tag: Tag) -> None:
    """
    No UniqueConstraint on (parent, ordering) exists: two sibling groups may share the same
    `ordering` value. A parent's clean() cannot see its own future children at save time (a
    child's FK can't point at a not-yet-existing parent row), so there is no single-row state to
    check a per-parent uniqueness rule against, and none is declared. See ADR-0002 Decision 2.
    """
    unique_constraints = [
        c for c in CompetencyCriteriaGroup._meta.constraints if isinstance(c, models.UniqueConstraint)
    ]
    assert not any({"parent", "ordering"} <= set(c.fields) for c in unique_constraints)

    parent = CompetencyCriteriaGroup.objects.create(tag=tag)
    sibling_a = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    sibling_b = CompetencyCriteriaGroup.objects.create(tag=tag, parent=parent, ordering=1)
    assert sibling_a.ordering == sibling_b.ordering == 1


# ---------------------------------------------------------------------------------------------
# Indexes and history


# ---------------------------------------------------------------------------------------------


def test_the_database_carries_adr_0002_decision_5_indexes_1_and_2() -> None:
    """
    The real table carries ADR-0002 Decision 5's index 1, the composite (tag, course), and index
    2 on parent. Index 2 comes from Django's automatic per-ForeignKey index rather than an
    explicit models.Index, so this introspects the database rather than the model.

    Compares the ordered column list, not a set: column order is the whole point of a composite
    index. An index on (course_id, oel_tagging_tag_id) would satisfy a set comparison just as
    well, but only the tag-first ordering also serves tag-only lookups.
    """
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, CompetencyCriteriaGroup._meta.db_table
        )

    def is_indexed(columns: list[str]) -> bool:
        return any(c["columns"] == columns and c["index"] for c in constraints.values())

    assert is_indexed(["oel_tagging_tag_id", "course_id"])
    assert is_indexed(["parent_id"])


def test_editing_a_group_writes_a_historical_row(tag: Tag) -> None:
    """
    HistoricalRecords() is applied to CompetencyCriteriaGroup: the Historical model is registered
    under its expected name, and creating then editing a group leaves two rows in it. See
    ADR-0003 Decision 1.

    The Historical model is looked up through the app registry rather than the `.history`
    attribute because simple_history installs `.history` as a runtime descriptor with no type
    stubs, which mypy cannot type.
    """
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    group = CompetencyCriteriaGroup.objects.create(tag=tag)

    group.name = "Poetry Mastery"
    group.save()

    assert historical_group.objects.filter(id=group.pk).count() == 2


def test_history_not_recorded_for_tag_taxonomy_or_competencytaxonomy(competency_taxonomy: CompetencyTaxonomy) -> None:
    """
    django-simple-history is NOT applied to oel_tagging_tag, oel_tagging_taxonomy, or
    CompetencyTaxonomy: none of the three has a `.history` attribute, and no Historical* model is
    registered for any of them. See ADR-0003 Decisions 1 and 2 for why history tracking stops at
    the CBE-specific models and does not reach back into the generic tagging models they build on.
    """
    assert not hasattr(Tag, "history")
    assert not hasattr(Taxonomy, "history")
    assert not hasattr(competency_taxonomy, "history")

    for app_label, model_name in [
        ("oel_tagging", "HistoricalTag"),
        ("oel_tagging", "HistoricalTaxonomy"),
        ("openedx_learning", "HistoricalCompetencyTaxonomy"),
    ]:
        with pytest.raises(LookupError):
            apps.get_model(app_label, model_name)
