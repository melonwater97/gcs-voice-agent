"""Verify signed authority and replay protection without sending MAVLink commands."""
import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

try:
    from uav.flight_backend import SimulationBackend, MavlinkConnection
except ImportError as error:
    raise unittest.SkipTest('Run in uav/.venv-wsl with flight dependencies') from error


class FlightBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name); self.key = b'x' * 32
        (self.directory / 'key').write_bytes(self.key)
        self.backend = SimulationBackend({'authority_key_file': str(self.directory / 'key'), 'target_system': 1}, self.directory)
        self.position = {'latitude': 47.397971, 'longitude': 8.546164, 'altitudeAmslM': 488}
        self.status = {'vehicleId': 'SITL:test', 'armed': False, 'landed': True, 'position': self.position}
        self.plan = {'mission': {'hoverSpeed': 5, 'items': [{'type': 'SimpleItem', 'command': 22, 'frame': 3, 'autoContinue': True,
                                                         'params': [0, 0, 0, None, self.position['latitude'], self.position['longitude'], 12]}]}}
        self.backend.status = Mock(side_effect=lambda: self.status.copy())
        self.backend.bridge = Mock(return_value={'digest': 'plan-one', 'revision': 1, 'plan': self.plan})
        self.items = MavlinkConnection.mission_items(self.plan)
        self.backend.link = Mock()
        self.backend.link.mission_items.side_effect = MavlinkConnection.mission_items
        self.backend.link.read_mission.return_value = self.items
        self.backend.link.matching.side_effect = MavlinkConnection.matching

    def grant(self, action='upload', **changes):
        value = {'nonce': str(uuid4()), 'flightId': str(uuid4()), 'action': action, 'expiresAt': int(time.time()*1000)+60000,
                 'vehicleId': 'SITL:test', 'planDigest': 'plan-one', 'qgcRevision': 1, 'origin': self.position,
                 'completion': 'return_and_land', 'payload': {},
                 'limits': {'maxAltitudeM': 100, 'maxSpeedMS': 10, 'maxDistanceM': 500, 'maxDurationS': 600}, **changes}
        body = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        signature = hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()
        return body + '.' + signature

    def test_bad_signature_expired_wrong_vehicle_and_draft_do_not_write(self):
        with self.assertRaises(ValueError):
            self.backend.execute(self.grant()[:-2] + 'zz')
        for changes in [{'expiresAt': 0}, {'vehicleId': 'other'}, {'planDigest': 'changed'}, {'qgcRevision': 2}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.backend.execute(self.grant(**changes))
        self.backend.link.upload.assert_not_called()
        self.backend.link.command.assert_not_called()

    def test_upload_replays_return_receipt_without_reupload_or_arming(self):
        token = self.grant()
        first = self.backend.execute(token); second = self.backend.execute(token)
        self.assertEqual(first, second)
        self.assertTrue(first['verified'])
        self.backend.link.upload.assert_called_once()
        self.backend.link.command.assert_not_called()

    def test_mismatched_readback_consumes_authority_and_cannot_be_retried(self):
        self.backend.link.read_mission.return_value = []
        token = self.grant()
        with self.assertRaisesRegex(RuntimeError, '读回'):
            self.backend.execute(token)
        with self.assertRaisesRegex(ValueError, '禁止重放'):
            self.backend.execute(token)
        self.backend.link.upload.assert_called_once()

    def test_start_requires_prior_matching_upload_and_its_own_grant(self):
        self.backend.start = Mock(return_value={'executionId': 'test'})
        with self.assertRaisesRegex(RuntimeError, '拒绝启动'):
            self.backend.execute(self.grant('start'))
        self.backend.start.assert_not_called()
        self.backend.execute(self.grant('upload'))
        self.backend.execute(self.grant('start'))
        self.backend.start.assert_called_once()

    def test_ground_state_and_actual_position_are_rechecked(self):
        self.status['armed'] = True
        with self.assertRaises(ValueError): self.backend.execute(self.grant())
        self.status['armed'] = False; self.status['position'] = {**self.position, 'latitude': self.position['latitude'] + .001}
        with self.assertRaises(ValueError): self.backend.execute(self.grant())
        self.backend.link.upload.assert_not_called()

    def test_limits_are_enforced_by_server_as_well_as_client(self):
        with self.assertRaisesRegex(ValueError, '高度'):
            self.backend.execute(self.grant(limits={'maxAltitudeM': 5, 'maxSpeedMS': 10, 'maxDistanceM': 500, 'maxDurationS': 600}))
        self.backend.link.upload.assert_not_called()


class FlightSdkSerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_server_schema_and_structured_status_without_network(self):
        from mcp import Client
        from uav.flight_server import create_server
        backend = Mock()
        backend.status.return_value = {'simulationVerified': True, 'vehicleId': 'SITL:isolated-fixture'}
        with tempfile.TemporaryDirectory() as directory, patch('uav.flight_server.SimulationBackend', return_value=backend):
            server = create_server({}, Path(directory))
            async with Client(server) as client:
                tools = await client.list_tools()
                self.assertIn('execute_authorized_action', [tool.name for tool in tools.tools])
                result = await client.call_tool('query_status', {})
                self.assertEqual(result.structured_content, backend.status.return_value)
        backend.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
