"""
Tests related to the Catalog models (CatalogCourse, CourseRun)
"""

import ddt  # type: ignore[import]
import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from opaque_keys.edx.locator import CourseLocator
from organizations.api import ensure_organization  # type: ignore[import]
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun


@ddt.ddt
class TestCatalogCourseModel(TestCase):
    """
    Low-level tests of the CatalogCourse model.
    """

    @classmethod
    def setUpClass(cls):
        ensure_organization("Org1")
        return super().setUpClass()

    # org field tests:

    def test_invalid_org(self) -> None:
        """Organization must exist in the DB before CatalogCourse can be created"""

        def create():
            CatalogCourse.objects.create(org_code="NewOrg", course_code="Python100")

        with pytest.raises(Organization.DoesNotExist):
            create()
        ensure_organization("NewOrg")
        create()

    # course_code field tests:

    def test_course_code_unique(self) -> None:
        """
        Test that course code is unique per org.
        """
        course_code = "Python100"
        ensure_organization("Org2")
        CatalogCourse.objects.create(org_code="Org1", course_code=course_code)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CatalogCourse.objects.create(org_code="Org1", course_code=course_code)
        # But different org is fine:
        CatalogCourse.objects.create(org_code="Org2", course_code=course_code)

    def test_course_code_case_sensitive(self) -> None:
        """
        Test that we cannot have two different catalog courses whose course code differs only by case.
        """
        org_code = "Org1"
        CatalogCourse.objects.create(org_code=org_code, course_code="Python100")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CatalogCourse.objects.create(org_code=org_code, course_code="pYTHon100")

    def test_course_code_required(self) -> None:
        """Test that course_code cannot be blank"""
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100", display_name="Python 100")
        with pytest.raises(IntegrityError):
            # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
            CatalogCourse.objects.filter(pk=cc.pk).update(course_code="")

    # display_name field tests:

    def test_display_name_default(self) -> None:
        """Test that display_name has a default"""
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
        assert cc.display_name == "Python100"

    def test_display_name_required(self) -> None:
        """Test that display_name cannot be blank"""
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100", display_name="Python 100")
        with pytest.raises(IntegrityError):
            # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
            CatalogCourse.objects.filter(pk=cc.pk).update(display_name="")

    def test_display_name_unicode(self) -> None:
        """Test that display_name can handle any valid unicode value"""
        # If it works with emojis, it should work with any human language characters.
        display_name = "Happy 😊"
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="HAPPY", display_name=display_name)
        cc.refresh_from_db()
        assert cc.display_name == display_name

    # language code field tests:

    def test_language_code_default(self) -> None:
        """Test that 'language' has a default"""
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
        # Our test settings don't specify a LANGUAGE_CODE, so this should be the Django default of "en-us"
        # https://docs.djangoproject.com/en/6.0/ref/settings/#language-code
        assert cc.language == "en-us"

    @override_settings(LANGUAGE_CODE="fr")
    def test_language_code_default2(self) -> None:
        """Test that 'language' gets its default from settings"""
        cc = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
        assert cc.language == "fr"

    @ddt.data(
        # ✅ Valid language codes:
        ("en", True),
        ("en-us", True),
        ("fr", True),
        ("pt-br", True),
        ("ca@valencia", True),  # This is one of the valid values in openedx-platform's default LANGUAGES setting
        # ❌ Invalid language codes:
        ("", False),
        ("x", False),
        ("EN", False),  # must be lowercase
        ("en-US", False),  # must be lowercase
        ("en_us", False),  # hyphen, not underscore, for consistency
        ("English", False),
        ("english", False),
    )
    @ddt.unpack
    def test_language_code_validation(self, language_code: str, valid: bool) -> None:
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


@ddt.ddt
class TestCourseRunModel(TestCase):
    """
    Low-level tests of the CourseRun model.
    """

    @classmethod
    def setUpClass(cls):
        ensure_organization("Org1")
        cls.catalog_course = CatalogCourse.objects.create(org_code="Org1", course_code="Python100")
        return super().setUpClass()

    # course ID tests:

    def test_course_id_autogenerated(self) -> None:
        """
        Test that course_id is auto-generated (optionally, for convenience)

        Among other things, this allows users to create course runs from the Django admin, without having to know the
        details of how to format the course IDs.
        """
        org_code = self.catalog_course.org_code
        course_code = self.catalog_course.course_code
        run = "Fall2026"
        cr = CourseRun.objects.create(catalog_course=self.catalog_course, run=run)
        cr.refresh_from_db()
        assert isinstance(cr.course_id, CourseLocator)
        assert str(cr.course_id) == f"course-v1:{org_code}+{course_code}+{run}"

    def test_course_id_run_match(self) -> None:
        """Test that course_id must match the org and course from the related CatalogCourse"""
        # Note: "run" is tested separately, in "test_run_exact" below.
        org_code = self.catalog_course.org_code
        course_code = self.catalog_course.course_code
        run = "Fall2026"

        def create_with(**kwargs):
            id_args = {"org": org_code, "course": course_code, "run": run, **kwargs}
            obj = CourseRun.objects.create(
                catalog_course=self.catalog_course, run=run, course_id=CourseLocator(**id_args)
            )
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

    def test_run_unique(self) -> None:
        """
        Test that run is unique per catalog course.
        """
        run = "Fall2026"
        catalog_course2 = CatalogCourse.objects.create(org_code="Org1", course_code="Systems300")
        CourseRun.objects.create(catalog_course=self.catalog_course, run=run)
        # Creating different catalog course with the same run name is fine:
        CourseRun.objects.create(catalog_course=catalog_course2, run=run)
        # But creating another run with the same catalog course and run name is not:
        with pytest.raises(IntegrityError):
            CourseRun.objects.create(catalog_course=self.catalog_course, run=run)

    def test_run_case_insensitive(self) -> None:
        """
        Test that we cannot have two different course runs whose run code
        differs only by case.

        We would like to support this, but we cannot, because we have a lot of legacy parts of the system that store
        course IDs without case sensitivity.
        """
        CourseRun.objects.create(catalog_course=self.catalog_course, run="fall2026")
        with pytest.raises(IntegrityError):
            CourseRun.objects.create(catalog_course=self.catalog_course, run="FALL2026")

    def test_run_required(self) -> None:
        """Test that run cannot be blank"""
        course = CourseRun.objects.create(catalog_course=self.catalog_course, run="fall2026")
        with pytest.raises(IntegrityError):
            # Using .update() will bypass all checks and defaults in save()/clean(), to see if the DB enforces this:
            CourseRun.objects.filter(pk=course.pk).update(run="")

    def test_run_exact(self) -> None:
        """Test that `run` must exactly match the run of the course ID (including case)"""
        catalog_course = CatalogCourse.objects.create(org_code="Org1", course_code="Test302")
        course_id = CourseLocator(org="Org1", course="Test302", run="MixedCase")

        with pytest.raises(IntegrityError, match="oex_catalog_courserun_courseid_run_match_exactly"):
            with transaction.atomic():
                CourseRun.objects.create(catalog_course=catalog_course, course_id=course_id, run="mixedcase")

        run = CourseRun.objects.create(catalog_course=catalog_course, course_id=course_id, run="MixedCase")

        # Do not allow modifying the run so it's completely different from the run in the course ID
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CourseRun.objects.filter(pk=run.pk).update(run="foobar")

        # Do not allow modifying the run so it doesn't match the course ID:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CourseRun.objects.filter(pk=run.pk).update(run="mixedcase")

        # Do not allow modifying the course ID so it doesn't match the run:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CourseRun.objects.filter(pk=run.pk).update(
                    course_id=CourseLocator(org="Org1", course="Test302", run="mixedcase"),
                )
