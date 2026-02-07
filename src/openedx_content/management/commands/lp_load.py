"""
Django management commands to handle restore learning packages (WIP)
"""
import logging
import time

from django.contrib.auth import get_user_model
from django.core.management import CommandError
from django.core.management.base import BaseCommand

from openedx_content.applets.backup_restore.api import load_learning_package

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    """
    Django management command to load a learning package from a zip file.
    """
    help = 'Load a learning package from a zip file.'

    def add_arguments(self, parser):
        parser.add_argument('file_name', type=str, help='The path of the input zip file to load.')
        parser.add_argument('username', type=str, help='The username of the user performing the load operation.')

    def handle(self, *args, **options):
        file_name = options['file_name']
        username = options['username']
        if not file_name.lower().endswith(".zip"):
            raise CommandError("Input file name must end with .zip")
        try:
            start_time = time.time()
            # Get the user performing the operation
            user = User.objects.get(username=username)

            result = load_learning_package(file_name, user=user)
            duration = time.time() - start_time
            if result["status"] == "error":
                message = "Errors encountered during restore:\n"
                log_buffer = result.get("log_file_error")
                if log_buffer:
                    message += log_buffer.getvalue()
                raise CommandError(message)
            message = f'{file_name} loaded successfully (duration: {duration:.2f} seconds)'
            self.stdout.write(self.style.SUCCESS(message))
        except FileNotFoundError as exc:
            message = f"Learning package file {file_name} not found: {exc}"
            raise CommandError(message) from exc
        except Exception as e:
            message = f"Failed to load '{file_name}': {e}"
            logger.exception(
                "Failed to load zip file %s ",
                file_name,
            )
            raise CommandError(message) from e
