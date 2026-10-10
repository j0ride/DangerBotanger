import os
from pathlib import Path
import unittest
from unittest.mock import patch

from dangerbot.paths import application_directory


class ApplicationDirectoryTests(unittest.TestCase):
    def test_source_keeps_existing_project_directory(self):
        with patch('dangerbot.paths.sys.frozen', False, create=True):
            self.assertEqual(application_directory(), Path.cwd())

    def test_packaged_windows_app_uses_user_storage_independent_of_working_directory(self):
        with patch('dangerbot.paths.sys.frozen', True, create=True), \
                patch('dangerbot.paths.sys.platform', 'win32'), \
                patch.dict(os.environ, {'LOCALAPPDATA': str(Path.cwd() / 'build' / 'user-data')}):
            self.assertEqual(application_directory(), Path.cwd() / 'build' / 'user-data' / 'DangerBotanger')

    def test_packaged_windows_storage_has_fallback_without_localappdata(self):
        home = Path.cwd() / 'build' / 'test-home'
        with patch('dangerbot.paths.sys.frozen', True, create=True), \
                patch('dangerbot.paths.sys.platform', 'win32'), patch.dict(os.environ, {}, clear=True), \
                patch('dangerbot.paths.Path.home', return_value=home):
            self.assertEqual(application_directory(), home / 'AppData' / 'Local' / 'DangerBotanger')
