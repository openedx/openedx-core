"""
These settings are here to use during tests, because django requires them.

In a real-world use case, apps in this project are installed into other
Django applications, so these settings will not be used.
"""

from os.path import abspath, dirname, join

from openedx_content.settings_api import openedx_content_backcompat_apps_to_install


def root(*args):
    """
    Get the absolute path of the given path relative to the project root.
    """
    return join(abspath(dirname(__file__)), *args)


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# If you provision the 'oel'@'%' with broad permissions on your MySQL instance,
# running the tests will auto-generate a database for running tests. This is
# slower than the default sqlite3 setup above, but it's sometimes helpful for
# finding things that only break in CI.
#
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.mysql",
#         "USER": "oel",
#         "PASSWORD": "oel-test-pass",
#         "HOST": "mysql",
#         "PORT": "3306",
#     }
# }

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    # Admin
    'django.contrib.admin',
    'django.contrib.admindocs',
    # Debugging
    "debug_toolbar",
    # django-rules based authorization
    'rules.apps.AutodiscoverRulesConfig',
    # Our own apps
    "openedx_tagging",
    "openedx_content",
    *openedx_content_backcompat_apps_to_install(),
]

AUTHENTICATION_BACKENDS = [
    'rules.permissions.ObjectPermissionBackend',
]

LOCALE_PATHS = [
    root("conf", "locale"),
]

ROOT_URLCONF = "projects.urls"

SECRET_KEY = "insecure-secret-key"

USE_TZ = True

MEDIA_ROOT = root("test_media")

# ========================= Django Rest Framework ========================

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'edx_rest_framework_extensions.paginators.DefaultPagination',
    'PAGE_SIZE': 10,
}

# ========================= LEARNING CORE SETTINGS ========================

# TODO: Document & rename this setting (https://github.com/openedx/openedx-core/issues/481)
OPENEDX_LEARNING = {
    'MEDIA': {
        'BACKEND': 'django.core.files.storage.InMemoryStorage',
        'OPTIONS': {
            'location': MEDIA_ROOT + "_private"
        }
    }
}

STATIC_URL = 'static/'
