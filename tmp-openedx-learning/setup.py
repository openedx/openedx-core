#!/usr/bin/env python
"""
Builds a final shell release of openedx-learning which simply 'redirects' to openedx-core
"""
import os
import sys

from setuptools import find_packages, setup

# We choose a version *between* the final openedx-learning version and the first openedx-core
# version. That way, openedx-learning gets a final release (this one), and there's an obvious
# continuity into openedx-core versioning.
VERSION = "0.33.0"

if sys.argv[-1] == 'tag':
    print("Tagging the version on github:")
    os.system("git tag -a %s -m 'version %s'" % (VERSION, VERSION))
    os.system("git push --tags")
    sys.exit()

setup(
    name='openedx-learning',
    version=VERSION,
    description="""Open edX Learning Core (and Tagging)""",
    long_description="""
**This package has been renamed to [openedx-core](https://pypi.org/project/openedx-core/)!**
"""
    author='David Ormsbee',
    author_email='dave@axim.org',
    url='https://github.com/openedx/openedx-learning',
    packages=[],
    include_package_data=True,
    install_requires=["openedx-core==0.34.0"],
    python_requires=">=3.11",
    license="AGPL 3.0",
    zip_safe=False,
    keywords='Python edx',
    classifiers=[
        'Development Status :: 7 - Inactive',
        'Framework :: Django',
        'Framework :: Django :: 4.2',
        'Framework :: Django :: 5.2',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
    ],
)
