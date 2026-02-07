"""
Quick and hacky management command to dump Component data into our model for
experimentation purposes. This lives in its own app because it's not intended to
be a part of this repo in the longer term. Think of this as just an example app
to validate the data model can do what we need it to do.

This script manipulates the data models directly, instead of using stable API
calls. This is only because those APIs haven't been created yet, and this is
trying to validate basic questions about the data model. This is not how apps
are intended to use openedx-learning in the longer term.

Open Question: If the data model is extensible, how do we know whether a change
has really happened between what's currently stored/published for a particular
item and the new value we want to set? For Content that's easy, because we
have actual hashes of the data. But it's not clear how that would work for
something like an ComponentVersion. We'd have to have some kind of mechanism where every
pp that wants to attach data gets to answer the question of "has anything
changed?" in order to decide if we really make a new ComponentVersion or not.
"""
from datetime import datetime, timezone
import logging
import mimetypes
import pathlib
import re
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Model references to remove
from openedx_content import api as content_api

SUPPORTED_TYPES = ["problem", "video", "html"]
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load sample Component data from course export"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.learning_package = None
        self.course_data_path = None
        self.init_known_types()

    def init_known_types(self):
        """
        Intialize mimetypes with some custom mappings we want to use.
        """
        # This is our own hacky video transcripts related format.
        mimetypes.add_type("application/vnd.openedx.srt+json", ".sjson")

        # Python's stdlib doesn't include these files that are sometimes used.
        mimetypes.add_type("text/markdown", ".md")
        mimetypes.add_type("image/svg+xml", ".svg")

        # Historically, JavaScript was "application/javascript", but it's now
        # officially "text/javascript"
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/javascript", ".mjs")

    def add_arguments(self, parser):
        parser.add_argument("course_data_path", type=pathlib.Path)
        parser.add_argument("learning_package_key", type=str)

    def handle(self, course_data_path, learning_package_key, **options):
        self.course_data_path = course_data_path
        self.learning_package_key = learning_package_key
        self.load_course_data(learning_package_key)

    def get_course_title(self):
        course_type_dir = self.course_data_path / "course"
        course_xml_file = next(course_type_dir.glob("*.xml"))
        course_root = ET.parse(course_xml_file).getroot()
        return course_root.attrib.get("display_name", "Unknown Course")

    def load_course_data(self, learning_package_key):
        print(f"Importing course from: {self.course_data_path}")
        now = datetime.now(timezone.utc)
        title = self.get_course_title()

        if content_api.learning_package_exists(learning_package_key):
            raise CommandError(
                f"{learning_package_key} already exists. "
                "This command currently only supports initial import."
            )

        with transaction.atomic():
            self.learning_package = content_api.create_learning_package(
                learning_package_key, title, created=now,
            )
            for block_type in SUPPORTED_TYPES:
                self.import_block_type(block_type, now) #, publish_log_entry)

            content_api.publish_all_drafts(
                self.learning_package.id,
                message="Initial Import from load_components script"
            )


    def create_content(self, static_local_path, now, component_version):
        key = pathlib.Path("static") / static_local_path
        real_path = self.course_data_path / key
        mime_type, _encoding = mimetypes.guess_type(key)
        if mime_type is None:
            logger.error(
                f'  no mimetype found for "{real_path}", defaulting to application/binary'
            )
            mime_type = "application/binary"

        try:
            data_bytes = real_path.read_bytes()
        except FileNotFoundError:
            logger.warning(f'  Static reference not found: "{real_path}"')
            return  # Might as well bail if we can't find the file.

        content = content_api.get_or_create_file_content(
            self.learning_package.id,
            data=data_bytes,
            mime_type=mime_type,
            created=now,
        )
        content_api.create_component_version_content(
            component_version,
            content.id,
            key=key,
        )

    def import_block_type(self, block_type_name, now): # , publish_log_entry):
        components_found = 0
        components_skipped = 0

        # Find everything that looks like a reference to a static file appearing
        # in attribute quotes, stripping off the querystring at the end. This is
        # not fool-proof as it will match static file references that are
        # outside of tag declarations as well.
        static_files_regex = re.compile(r"""['"]\/static\/(.+?)["'\?]""")
        block_data_path = self.course_data_path / block_type_name
        block_type = content_api.get_or_create_component_type("xblock.v1", block_type_name)

        for xml_file_path in block_data_path.glob("*.xml"):
            components_found += 1
            local_key = xml_file_path.stem

            # Do some basic parsing of the content to see if it's even well
            # constructed enough to add (or whether we should skip/error on it).
            try:
                block_root = ET.parse(xml_file_path).getroot()
            except ET.ParseError as err:
                logger.error(f"Parse error for {xml_file_path}: {err}")
                components_skipped += 1
                continue

            display_name = block_root.attrib.get("display_name", "")
            _component, component_version = content_api.create_component_and_version(
                self.learning_package.id,
                component_type=block_type,
                local_key=local_key,
                title=display_name,
                created=now,
                created_by=None,
            )

            # Create the Content entry for the raw data...
            text = xml_file_path.read_text('utf-8')
            text_content, _created = content_api.get_or_create_text_content(
                self.learning_package.id,
                text=text,
                mime_type=f"application/vnd.openedx.xblock.v1.{block_type_name}+xml",
                created=now,
            )
            # Add the OLX source text to the ComponentVersion
            content_api.create_component_version_content(
                component_version,
                text_content.pk,
                key="block.xml",
            )

            # Cycle through static assets references and add those as well...
            for static_local_path in static_files_regex.findall(text_content.text):
                self.create_content(static_local_path, now, component_version)

        print(f"{block_type}: {components_found} (skipped: {components_skipped})")
