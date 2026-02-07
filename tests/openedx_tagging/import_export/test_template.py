"""
Tests the import/export template files.
"""
from __future__ import annotations

import os

import ddt  # type: ignore[import]
from django.test.testcases import TestCase

from openedx_tagging.api import get_tags
from openedx_tagging.import_export import ParserFormat
from openedx_tagging.import_export import api as import_api

from ..utils import pretty_format_tags
from .mixins import TestImportExportMixin


@ddt.ddt
class TestImportTemplate(TestImportExportMixin, TestCase):
    """
    Test the CSV import/export template.
    """

    def open_template_file(self, template_file):
        """
        Returns an open file handler for the template file.
        """
        dirname = os.path.dirname(__file__)
        filename = os.path.join(
            dirname,
            '../../..',
            template_file,
        )
        return open(filename, "rb")

    @ddt.data(
        ('src/openedx_tagging/import_export/template.csv', ParserFormat.CSV),
        ('src/openedx_tagging/import_export/template.json', ParserFormat.JSON),
    )
    @ddt.unpack
    def test_import_template(self, template_file, parser_format):
        with self.open_template_file(template_file) as import_file:
            result, _task, _plan = import_api.import_tags(
                self.taxonomy,
                import_file,
                parser_format,
                replace=True,
            )
            assert result, import_api.get_last_import_log(self.taxonomy)

        assert pretty_format_tags(get_tags(self.taxonomy), external_id=True) == [
            'Electronic instruments (ELECTRIC) (None) (children: 2)',
            '  Synthesizer (SYNTH) (Electronic instruments) (children: 0)',
            '  Theramin (THERAMIN) (Electronic instruments) (children: 0)',
            'Percussion instruments (PERCUSS) (None) (children: 3 + 6)',
            '  Chordophone (CHORD) (Percussion instruments) (children: 1)',
            '    Piano (PIANO) (Chordophone) (children: 0)',
            '  Idiophone (BELLS) (Percussion instruments) (children: 2)',
            '    Celesta (CELESTA) (Idiophone) (children: 0)',
            '    Hi-hat (HI-HAT) (Idiophone) (children: 0)',
            '  Membranophone (DRUMS) (Percussion instruments) (children: 2 + 1)',
            '    Cajón (CAJÓN) (Membranophone) (children: 1)',
            # This tag is present in the import files, but it will be omitted from get_tags()
            # because it sits beyond TAXONOMY_MAX_DEPTH.
            #      Pyle Stringed Jam Cajón (PYLE) (Cajón) (children: 0)
            '    Tabla (TABLA) (Membranophone) (children: 0)',
            'String instruments (STRINGS) (None) (children: 2 + 5)',
            '  Bowed strings (BOW) (String instruments) (children: 2)',
            '    Cello (CELLO) (Bowed strings) (children: 0)',
            '    Violin (VIOLIN) (Bowed strings) (children: 0)',
            '  Plucked strings (PLUCK) (String instruments) (children: 3)',
            '    Banjo (BANJO) (Plucked strings) (children: 0)',
            '    Harp (HARP) (Plucked strings) (children: 0)',
            '    Mandolin (MANDOLIN) (Plucked strings) (children: 0)',
            'Wind instruments (WINDS) (None) (children: 2 + 5)',
            '  Brass (BRASS) (Wind instruments) (children: 2)',
            '    Trumpet (TRUMPET) (Brass) (children: 0)',
            '    Tuba (TUBA) (Brass) (children: 0)',
            '  Woodwinds (WOODS) (Wind instruments) (children: 3)',
            '    Clarinet (CLARINET) (Woodwinds) (children: 0)',
            '    Flute (FLUTE) (Woodwinds) (children: 0)',
            '    Oboe (OBOE) (Woodwinds) (children: 0)',
        ]
