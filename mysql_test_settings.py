"""
This is an extension of the default test_settings.py file that uses MySQL for
the backend. While the openedx-learning apps should run fine using SQLite, they
also do some MySQL-specific things around charset/collation settings and row
compression.

The tox targets for py311-django42 and py312-django42 will use this settings file.
For the most part, you can use test_settings.py instead (that's the default if
you just run "pytest" with no arguments).

If you need a compatible MySQL server running locally, spin one up with:
docker run --rm \
    -e MYSQL_DATABASE=test_oel_db \
    -e MYSQL_USER=test_oel_user \
    -e MYSQL_PASSWORD=test_oel_pass \
    -e MYSQL_RANDOM_ROOT_PASSWORD=true \
    -p 3306:3306 mysql:8
"""

from test_settings import *  # pylint: disable=wildcard-import

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "oel_db",
        "USER": "test_oel_user",
        "PASSWORD": "test_oel_pass",
        "HOST": "127.0.0.1",
        "PORT": "3306",
        "OPTIONS": {
            "charset": "utf8mb4"
        }
    }
}
