"""
Django management commands to handle backup learning packages (WIP)
"""
import logging
import time

from django.contrib.auth import get_user_model
from django.core.management import CommandError
from django.core.management.base import BaseCommand

from openedx_content.applets.backup_restore.api import create_zip_file
from openedx_content.applets.publishing.api import LearningPackage

logger = logging.getLogger(__name__)


User = get_user_model()


class Command(BaseCommand):
    """
    Django management command to export a learning package to a zip file.
    """
    help = 'Export a learning package to a zip file.'

    def add_arguments(self, parser):
        parser.add_argument('lp_key', type=str, help='The key of the LearningPackage to dump')
        parser.add_argument('file_name', type=str, help='The name of the output zip file')
        parser.add_argument(
            '--username',
            type=str,
            help='The username of the user performing the backup operation.',
            default=None
        )
        parser.add_argument(
            '--origin_server',
            type=str,
            help='The origin server for the backup operation.',
            default=None
        )

    def handle(self, *args, **options):
        lp_key = options['lp_key']
        file_name = options['file_name']
        username = options['username']
        origin_server = options['origin_server']
        if not file_name.lower().endswith(".zip"):
            raise CommandError("Output file name must end with .zip")
        try:
            # Get the user performing the operation
            user = None
            if username:
                user = User.objects.get(username=username)
            start_time = time.time()
            create_zip_file(lp_key, file_name, user=user, origin_server=origin_server)
            elapsed = time.time() - start_time
            message = f'{lp_key} written to {file_name} (create_zip_file: {elapsed:.2f} seconds)'
            self.stdout.write(self.style.SUCCESS(message))
        except LearningPackage.DoesNotExist as exc:
            message = f"Learning package with key {lp_key} not found"
            raise CommandError(message) from exc
        except Exception as e:
            message = f"Failed to export learning package '{lp_key}': {e}"
            logger.exception(
                "Failed to create zip file %s (learning‑package key %s)",
                file_name,
                lp_key,
            )
            raise CommandError(message) from e
