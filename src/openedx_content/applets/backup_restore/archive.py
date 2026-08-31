"""
This module exists to abstract away the container archive format.

We rely on ``fsspec`` to do all the hard work of abstracting away the filesystem
access. This gives us a tremendous amount of flexibility, but for now we're only
using two types: a Zip archive for normal use, and a Directory-based filesystem
to make testing and debugging simpler.

Note that the Zip file is a departure from the earlier Open edX Platform
practice of using .tar.gz files for course imports. This was done because Zip
files are much easier for users running Windows and macOS to create.

For future consideration: Using LibArchiveFileSystem would allow us to support
tar.gz, zip, 7z, and a bunch of other archiving formats in read-only mode. I'm
not doing it now because I'm not clear on whether the reliance on libarchive
makes things problematic, I don't understand the performance implications, and I
don't want to open the door on "supported archive formats" to include everything
under the sun.

It's worth noting that fsspec has more exotic backends like GithubFileSystem,
which might simplify the workflow for some advanced users. The code for this is
easy enough to write—it's mostly about whether it's worth the overhead of
testing and maintaining over time.
"""
from pathlib import Path

from fsspec import AbstractFileSystem
from fsspec.implementations.dirfs import DirFileSystem
from fsspec.implementations.zip import ZipFileSystem

from .errors import ArchiveNotReadableError


def read_fs_for_path(path_str: str) -> AbstractFileSystem:
    """
    Return an fsspec filesystem that can handle the given ``path_str``.

    If the ``path_str`` passed in is a directory, we treat that as the root of
    the archive to be restored. Otherwise, we assume you're passing a Zip file.
    """
    path = Path(path_str)
    if path.is_dir():
        # read-only mode is not available for DirFileSystem
        return DirFileSystem(path)
    elif path.is_file() and path.suffix.lower() == ".zip":
        # read-only is the default for ZipFilesystem, but make it explicit
        return ZipFileSystem(path, mode="r")

    raise ArchiveNotReadableError(
        "Expected a directory or a .zip file", path=path_str
    )
