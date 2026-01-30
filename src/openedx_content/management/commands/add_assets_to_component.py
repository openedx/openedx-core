"""
Management command to add files to a Component.

This is mostly meant to be a debugging tool to let us to easily load some test
asset data into the system.
"""
import pathlib
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from ...api import create_next_component_version, get_component_by_key, get_learning_package_by_key


class Command(BaseCommand):
    """
    Add files to a Component, creating a new Component Version.

    This does not publish the the Component.

    Note: This is a quick debug tool meant to stuff some asset data into
    Open edX Core Content models for testing. It's not intended as a robust and
    performant tool for modifying actual production content, and should not be
    used for that purpose.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "learning_package_key",
            type=str,
            help="LearningPackage.key value for where the Component is located."
        )
        parser.add_argument(
            "component_key",
            type=str,
            help="Component.key that you want to add assets to."
        )
        parser.add_argument(
            "file_mappings",
            nargs="+",
            type=str,
            help=(
                "Mappings of desired Component asset paths to the disk paths "
                "of where to upload the file from, separated by ':'. (Example: "
                "static/donkey.jpg:/Users/dave/Desktop/donkey-big.jpg). A "
                "blank value for upload file means to remove that from the "
                "Component. You may upload/remove as many files as you want in "
                "a single invocation."
            )
        )

    def handle(self, *args, **options):
        """
        Add files to a Component as ComponentVersion -> Content associations.
        """
        learning_package_key = options["learning_package_key"]
        component_key = options["component_key"]
        file_mappings = options["file_mappings"]

        learning_package = get_learning_package_by_key(learning_package_key)
        # Parse something like: "xblock.v1:problem:area_of_circle_1"
        namespace, type_name, local_key = component_key.split(":", 2)
        component = get_component_by_key(
            learning_package.id, namespace, type_name, local_key
        )

        created = datetime.now(tz=timezone.utc)
        local_keys_to_content_bytes = {}

        for file_mapping in file_mappings:
            local_key, file_path = file_mapping.split(":", 1)

            local_keys_to_content_bytes[local_key] = pathlib.Path(file_path).read_bytes() if file_path else None

        next_version = create_next_component_version(
            component.pk,
            content_to_replace=local_keys_to_content_bytes,
            created=created,
        )

        self.stdout.write(
            f"Created v{next_version.version_num} of "
            f"{next_version.component.key} ({next_version.uuid}):"
        )
        for cvc in next_version.componentversioncontent_set.all():
            self.stdout.write(f"- {cvc.key} ({cvc.uuid})")
