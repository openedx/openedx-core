"""
Tests related to the Catalog models (CatalogCourse, CourseRun)
"""
# pylint: disable=unused-argument
# mypy: disable-error-code="misc"
# (Ignore 'Unexpected attribute "org_code" for model "CatalogCourse"' until
#  https://github.com/typeddjango/django-stubs/issues/1034 is fixed.)

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import override_settings
from opaque_keys.edx.locator import CourseLocator
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.models import Organization  # type: ignore[import]

from openedx_catalog.models import CatalogCourse, CourseRun

pytestmark = pytest.mark.django_db


@pytest.fixture(name="org1")
def _org1() -> None:
    """Create an "Org1" organization for use in these tests"""
    ensure_organization("Org1")


@pytest.fixture(name="org2")
def _org2() -> None:
    """Create an "Org2" organization for use in these tests"""
    ensure_organization("Org2")


@pytest.fixture(name="python100")
def _python100(org1) -> CatalogCourse:
    """Create a CatalogCourse for use in these tests"""
    return CatalogCourse.objects.create(org_code="Org1", course_code="Python100")


# Low-level tests of the CatalogCourse model.


def test_invalid_org() -> None:
    """Organization must exist in the DB before CatalogCourse can be created"""

    def create():
        CatalogCourse.objects.create(org_code="NewOrg", course_code="Python100")

    with pytest.raises(Organization.DoesNotExist):
        create()
    ensure_organization("NewOrg")
    create()


# course_code field tests:


def test_course_code_unique(org1, org2) -> None:
    """
    Test that course code is unique per org.
    """
    course_code = "Python100"
    CatalogCourse.objects.create(org_code="Org1", course_code=course_code)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CatalogCourse.objects.create(org_code="Org1", course_code=course_code)
    # But different org is fine:
    CatalogCourse.objects.create(org_code="Org2", course_code=course_code)


def test_course_code_case_sensitive(org1) -> None:
    """
    Test that we cannot have two different catalog courses whose course code differs only by case.
    """
    org_code = "Org1"
    CatalogCourse.objects.create(org_code=org_code, course_code="Python100")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CatalogCourse.objects.create(org_code=org_code, course_code="pYTHon100")


def test_course_code_required(org1) -> None:
    """Test that course_code cannot be blank"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100", display_name="Python 100")
    with pytest.raises(IntegrityError):
        # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
        CatalogCourse.objects.filter(pk=cc.pk).update(course_code="")


# url_slug field tests:
def test_url_slug(org1) -> None:
    """Test that url_slug is generated automatically"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
    assert cc.url_slug == "Org1:Python100"


# display_name field tests:


def test_display_name_default(org1) -> None:
    """Test that display_name has a default"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
    assert cc.display_name == "Python100"


def test_display_name_required(org1) -> None:
    """Test that display_name cannot be blank"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100", display_name="Python 100")
    with pytest.raises(IntegrityError):
        # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
        CatalogCourse.objects.filter(pk=cc.pk).update(display_name="")


def test_display_name_unicode(org1) -> None:
    """Test that display_name can handle any valid unicode value"""
    # If it works with emojis, it should work with any human language characters.
    display_name = "Happy 😊"
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="HAPPY", display_name=display_name)
    cc.refresh_from_db()
    assert cc.display_name == display_name


# language code field tests:


def test_language_code_default(org1) -> None:
    """Test that 'language' has a default"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
    # Our test settings don't specify a LANGUAGE_CODE, so this should be the Django default of "en-us"
    # https://docs.djangoproject.com/en/6.0/ref/settings/#language-code
    assert cc.language == "en-us"


@override_settings(LANGUAGE_CODE="fr")
def test_language_code_default2(org1) -> None:
    """Test that 'language' gets its default from settings"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
    assert cc.language == "fr"


@pytest.mark.parametrize(
    "language_code,valid",
    [
        # ✅ Valid language codes:
        ("en", True),
        ("en-us", True),
        ("fr", True),
        ("fr-fr", True),
        ("fr-ca", True),
        ("pt-br", True),
        ("es-419", True),  # Spanish (Latin America)
        ("ca-es-valencia", True),  # Catalan (Valencia)
        # ❌ Invalid language codes:
        ("", False),
        ("x", False),
        ("EN", False),  # must be lowercase
        ("en-US", False),  # must be lowercase
        ("en_us", False),  # hyphen, not underscore, for consistency
        ("en--us", False),  # typo
        ("English", False),
        ("english", False),
        ("ca@valencia", False),  # Don't support old gettext-style locales; should be "ca-es-valencia"
    ],
)
def test_language_code_validation(language_code: str, valid: bool, org1) -> None:
    """Test that language codes must follow the prescribed format"""

    def create():
        CatalogCourse.objects.create(org_code="Org1", course_code="Python100", language=language_code)

    if valid:
        create()
    else:
        with pytest.raises(IntegrityError, match="oex_catalog_catalogcourse_language_regex"):
            with transaction.atomic():
                create()
        # And from the Django admin, we'd get a nicer error message:
        expected_msg = "The language code must be lowercase" if language_code else "This field cannot be blank."
        with pytest.raises(ValidationError, match=expected_msg):
            CatalogCourse(
                org_code="Org1", course_code="Python100", language=language_code, display_name="x"
            ).full_clean()


@pytest.mark.parametrize(
    "kwargs,expected_full_lang,expected_short",
    [
        # input field, expected .language value, expected .language_short value
        ({"language": "fr"}, "fr", "fr"),
        ({"language": "fr-ca"}, "fr-ca", "fr"),
        ({"language": "zh-cn"}, "zh-cn", "zh_HANS"),
        ({"language": "zh-hk"}, "zh-hk", "zh_HANT"),
        ({"language_short": "zh_HANS"}, "zh-cn", "zh_HANS"),
        ({"language_short": "zh_HANT"}, "zh-hk", "zh_HANT"),
        ({"language_short": "fr"}, "fr", "fr"),
        ({"language": "zh-hans"}, "zh-cn", "zh_HANS"),  # Input is invalid but gets corrected by clean()
        ({"language": "zh-hant"}, "zh-hk", "zh_HANT"),  # Input is invalid but gets corrected by clean()
    ],
)
def test_language_code_compatibility(kwargs: dict, expected_full_lang: str, expected_short: str, org1) -> None:
    """Test that the language_short field is fully backwards compatible with CourseOverview.language"""
    cc = CatalogCourse.objects.create(org_code="Org1", course_code="Locale100", **kwargs)
    assert cc.language == expected_full_lang
    assert cc.language_short == expected_short


################################################################################
# Low-level tests of the CourseRun model.

# course ID tests:


def test_course_key_autogenerated(python100: CatalogCourse) -> None:
    """
    Test that course_key is auto-generated (optionally, for convenience)

    Among other things, this allows users to create course runs from the Django admin, without having to know the
    details of how to format the course IDs.
    """
    org_code = python100.org_code
    course_code = python100.course_code
    run_code = "Fall2026"
    cr = CourseRun.objects.create(catalog_course=python100, run_code=run_code)
    cr.refresh_from_db()
    assert isinstance(cr.course_key, CourseLocator)
    assert str(cr.course_key) == f"course-v1:{org_code}+{course_code}+{run_code}"


def test_course_key_run_match(python100: CatalogCourse) -> None:
    """Test that course_key must match the org and course from the related CatalogCourse"""
    # Note: "run_code" is tested separately, in "test_run_code_exact" below.
    org_code = python100.org_code
    course_code = python100.course_code
    run_code = "Fall2026"

    def create_with(**kwargs):
        id_args = {"org": org_code, "course": course_code, "run": run_code, **kwargs}
        obj = CourseRun.objects.create(catalog_course=python100, run_code=run_code, course_key=CourseLocator(**id_args))
        obj.delete()

    # course code must match exactly:
    with pytest.raises(ValidationError):
        assert course_code.upper() != course_code
        create_with(course=course_code.upper())

    # The org cannot be completely different
    with pytest.raises(ValidationError):
        create_with(org="other_org")

    # But we DO allow the org to have different case:
    assert org_code.upper() != org_code
    create_with(org=org_code.upper())

    # And if everything case matches exactly, it works (normal situation):
    create_with()


# run field tests:


def test_run_code_unique(python100: CatalogCourse) -> None:
    """
    Test that run_code is unique per catalog course.
    """
    run_code = "Fall2026"
    catalog_course2 = CatalogCourse.objects.create(org_code="Org1", course_code="Systems300")
    CourseRun.objects.create(catalog_course=python100, run_code=run_code)
    # Creating different catalog course with the same run code is fine:
    CourseRun.objects.create(catalog_course=catalog_course2, run_code=run_code)
    # But creating another run with the same catalog course and run_code is not:
    with pytest.raises(IntegrityError):
        CourseRun.objects.create(catalog_course=python100, run_code=run_code)


def test_run_code_case_insensitive(python100: CatalogCourse) -> None:
    """
    Test that we cannot have two different course runs whose run code
    differs only by case.

    We would like to support this, but we cannot, because we have a lot of legacy parts of the system that store
    course IDs without case sensitivity.
    """
    CourseRun.objects.create(catalog_course=python100, run_code="fall2026")
    with pytest.raises(IntegrityError):
        CourseRun.objects.create(catalog_course=python100, run_code="FALL2026")


def test_run_code_required(python100: CatalogCourse) -> None:
    """Test that run_code cannot be blank"""
    course = CourseRun.objects.create(catalog_course=python100, run_code="fall2026")
    with pytest.raises(IntegrityError):
        # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
        CourseRun.objects.filter(pk=course.pk).update(run_code="")


def test_run_code_exact(org1) -> None:
    """Test that `run_code` must exactly match the run of the course key (including case)"""
    catalog_course = CatalogCourse.objects.create(org_code="Org1", course_code="Test302")
    course_key = CourseLocator(org="Org1", course="Test302", run="MixedCase")

    # Note: depending on whether we use .create()/.save() or .update(), we'll get either ValidationError or
    # IntegrityError.

    with pytest.raises(
        ValidationError, match="The CourseRun 'run_code' field should match the run in the 'course_key'."
    ):
        with transaction.atomic():
            CourseRun.objects.create(catalog_course=catalog_course, course_key=course_key, run_code="mixedcase")

    run = CourseRun.objects.create(catalog_course=catalog_course, course_key=course_key, run_code="MixedCase")

    # Do not allow modifying the run so it's completely different from the run in the course ID
    with pytest.raises(IntegrityError, match="oex_catalog_courserun_course_key_run_code_match_exactly"):
        with transaction.atomic():
            CourseRun.objects.filter(pk=run.pk).update(run_code="foobar")

    # Do not allow modifying the run so it doesn't match the course ID:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CourseRun.objects.filter(pk=run.pk).update(run_code="mixedcase")

    # Do not allow modifying the course ID so it doesn't match the run:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CourseRun.objects.filter(pk=run.pk).update(
                course_key=CourseLocator(org="Org1", course="Test302", run="mixedcase"),
            )


# TODO: it would be good to test here that CourseRun objects work correctly with CCX keys like
# "ccx-v1:org+code+run+ccx@1", but I don't want to introduce a dependency on edx-ccx-keys for now. Once we consolidate
# all the key types into a single repo, we can add a test here.
