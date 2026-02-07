"""Utility functions for working with in-memory zip files from folders."""

import io
import zipfile
from pathlib import Path


def folder_to_inmemory_zip(folder_path: str) -> zipfile.ZipFile:
    """
    Read the contents of a folder and returns an in-memory ZipFile object.

    Args:
        folder_path (str): Path to the folder to zip.

    Returns:
        zipfile.ZipFile: An in-memory ZipFile containing the folder's contents.
    """
    buffer = io.BytesIO()
    folder = Path(folder_path)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(folder)
                zipf.write(file_path, arcname=str(arcname))
    buffer.seek(0)
    return zipfile.ZipFile(buffer, "r")
