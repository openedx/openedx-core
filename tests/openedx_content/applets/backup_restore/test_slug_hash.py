"""
Test cases for the slugify_hashed_filename function.

These tests ensure consistent and predictable behavior when
generating slugified, hash-based filenames.
"""

from django.test import TestCase

from openedx_content.applets.backup_restore.zipper import slugify_hashed_filename


class SlugHashTestCase(TestCase):
    """
    Test the slugify_hashed_filename function.
    """

    def test_slugify_hashed_filename(self):
        # Test the slugify_hashed_filename function
        self.assertEqual(slugify_hashed_filename("my_example"), "my_example_880989")

    def test_slugify_hashed_filename_special_chars(self):
        # Test the slugify_hashed_filename function with special characters
        self.assertEqual(slugify_hashed_filename("my@ex#ample!"), "myexample_3366b5")

    def test_slugify_hashed_filename_invalid_characters_common_filesystems(self):
        # Test the slugify_hashed_filename function with invalid characters for common filesystems
        self.assertEqual(
            slugify_hashed_filename("xblock.v1:problem:my_component"), "xblockv1problemmy_component_d346b1"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1/problem/my_component"), "xblockv1problemmy_component_2648c6"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1?problem?my_component"), "xblockv1problemmy_component_12a86d"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1\\problem_[my_component]"), "xblockv1problem_my_component_a59bb3"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1>problem_>my_component"), "xblockv1problem_my_component_8497eb"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1*problem*EndsWith|"), "xblockv1problemendswith_b88aab"
        )
        self.assertEqual(
            slugify_hashed_filename("xblock.v1*problem*EndsWith."), "xblockv1problemendswith_7fa0cf"
        )

    def test_slugify_hashed_filename_unicode(self):
        # Test the slugify_hashed_filename function with unicode characters

        # Example with accents
        self.assertEqual(slugify_hashed_filename("café"), "café_07f4df")
        self.assertEqual(slugify_hashed_filename("naïve"), "naïve_308c7e")

        # Example with non-latin (e.g., Japanese)
        identifier = "テスト用"
        result = slugify_hashed_filename(identifier)
        self.assertEqual(result, "テスト用_be48ab")

        # Example with mixed characters
        identifier = "café_テスト用"
        result = slugify_hashed_filename(identifier)
        self.assertEqual(result, "café_テスト用_3cf9ef")

    def test_slugify_hashed_filename_long_string(self):
        # Test the slugify_hashed_filename function with a long string
        long_string = "a" * 100
        self.assertEqual(slugify_hashed_filename(long_string), f"{long_string}_4e84b3")

    def test_slugify_hashed_filename_case_insensitivity(self):
        # Test the slugify_hashed_filename function for case insensitivity
        upper_case_value = slugify_hashed_filename("MY_EXAMPLE")
        lower_case_value = slugify_hashed_filename("my_example")
        # The values should be different even though they are the same but with different cases
        self.assertNotEqual(upper_case_value, lower_case_value)
