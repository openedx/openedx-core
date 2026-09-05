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
    # Open edX Organizations (dependency for openedx_catalog)
    "organizations",
    # django-rules based authorization
    'rules.apps.AutodiscoverRulesConfig',
    # django-simple-history: registers its template tag libraries and admin integration
    # (SimpleHistoryAdmin) and its management commands (populate_history, clean_old_history,
    # clean_duplicate_history). HistoricalRecords() works without this app installed, but nothing
    # else it provides does, and the package ships no AppConfig or system check to warn you.
    "simple_history",
    # Our own apps
    "openedx_tagging",
    "openedx_content",
    "openedx_catalog",
    "openedx_learning",
    *openedx_content_backcompat_apps_to_install(),
    # Apps with models that are only used for testing
    "tests.test_django_app",
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

# Required for Django admin which is required because it's referenced by projects.urls (ROOT_URLCONF)
TEMPLATES = [
    {
        'NAME': 'django',
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Don't look for template source files inside installed applications.
        # 'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        # 'DIRS': [],
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.messages.context_processors.messages',
                'django.contrib.auth.context_processors.auth',
            ],
        }
    },
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
