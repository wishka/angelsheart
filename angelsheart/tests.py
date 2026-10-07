from unittest import mock

from django.db.utils import OperationalError
from django.test import TestCase

from angelsheart import health


class HealthTests(TestCase):
    def test_ok_with_revision(self):
        with mock.patch.object(health, 'revision', return_value='20261007-abc1234'):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'revision': '20261007-abc1234'})
        self.assertIn('no-cache', response['Cache-Control'])

    def test_database_down_is_503(self):
        with mock.patch.object(health.connection, 'ensure_connection', side_effect=OperationalError):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)

    def test_no_revision_file_means_dev(self):
        with mock.patch.object(health, 'REVISION_FILE', health.Path('/nonexistent/REVISION')):
            self.assertEqual(health.revision(), 'dev')
