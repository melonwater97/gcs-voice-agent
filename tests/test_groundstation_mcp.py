"""HTTP adapter configuration, confirmed failures, and durable uncertain retries."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from integrations.groundstation_http import GroundStationConfig, GroundStationHTTP, load_config
from simulator.http_server import GroundStationSimulator


TASK = {"task_type": "放飞前出任务", "parameters": {"mode": "原地起飞", "takeoff_altitude": 50}}


class GroundStationMcpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.service = GroundStationSimulator(self.directory / "station.json")
        self.config = GroundStationConfig(base_url="http://station.example:18003", simulation=True)
        self.client = GroundStationHTTP(self.config, self.directory / "requests")

    def response(self, url, *, json, **kwargs):
        self.assertEqual(url, "http://station.example:18003/api/tasks")
        status, body = self.service.handle(json)
        return httpx.Response(status, json=body)

    def test_three_stage_http_and_confirmed_errors(self):
        with patch("integrations.groundstation_http.httpx.post", side_effect=self.response):
            created = self.client.call("create_task", {"task": TASK})
            self.assertTrue(created["simulation"])
            self.assertEqual(created["status"], "draft")
            with self.assertRaisesRegex(ValueError, "当前状态") as caught:
                self.client.call("execute_task", {"task_id": created["task_id"]})
            self.assertNotIn("结果待确认", str(caught.exception))
            self.assertEqual(self.client.call("upload_task", {"task_id": created["task_id"]})["status"], "uploaded")
            self.assertEqual(self.client.call("execute_task", {"task_id": created["task_id"]})["status"], "executing")

    def test_timeout_after_commit_reuses_request_id_after_client_restart(self):
        sent = []

        def timeout(url, **kwargs):
            sent.append(kwargs["json"])
            self.response(url, **kwargs)  # Server committed; response was lost.
            raise httpx.ReadTimeout("lost response")

        with patch("integrations.groundstation_http.httpx.post", side_effect=timeout):
            with self.assertRaisesRegex(ValueError, "结果待确认"):
                self.client.call("create_task", {"task": TASK})
        restarted = GroundStationHTTP(self.config, self.directory / "requests")
        with patch("integrations.groundstation_http.httpx.post", side_effect=self.response):
            result = restarted.call("create_task", {"task": TASK})
        self.assertEqual(result["request_id"], sent[0]["header"]["msg_id"])
        saved = json.loads((self.directory / "station.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["tasks"]), 1)

    def test_expired_uncertain_request_is_not_resent(self):
        with patch("integrations.groundstation_http.httpx.post", side_effect=httpx.ReadTimeout("lost")):
            with self.assertRaises(ValueError):
                self.client.call("create_task", {"task": TASK})
        file = next((self.directory / "requests").glob("*.json"))
        entry = json.loads(file.read_text(encoding="utf-8")); entry["created_at"] = time.time() - 86401
        file.write_text(json.dumps(entry), encoding="utf-8")
        with patch("integrations.groundstation_http.httpx.post") as send:
            with self.assertRaisesRegex(ValueError, "超过 24 小时"):
                self.client.call("create_task", {"task": TASK})
            send.assert_not_called()

    def test_mismatched_reply_is_uncertain(self):
        def wrong_reply(url, **kwargs):
            response = self.response(url, **kwargs)
            body = response.json(); body["header"]["src"]["id"] = "wrong-station"
            return httpx.Response(200, json=body)
        with patch("integrations.groundstation_http.httpx.post", side_effect=wrong_reply):
            with self.assertRaisesRegex(ValueError, "结果待确认"):
                self.client.call("create_task", {"task": TASK})

    def test_yaml_validation_and_authentication(self):
        file = self.directory / "service.yaml"
        file.write_text("ground_station:\n  base_url: http://station.example:18003\n  simulation: false\n  token_env: TEST_GCS_TOKEN\n", encoding="utf-8")
        client = GroundStationHTTP(load_config(file), self.directory / "requests")
        with patch.dict("os.environ", {"TEST_GCS_TOKEN": "test-token"}), patch("integrations.groundstation_http.httpx.post", side_effect=self.response) as send:
            result = client.call("create_task", {"task": TASK})
            self.assertFalse(result["simulation"])
            self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
        for text in ["ground_station:\n  timeout_seconds: 0", "ground_station:\n  token: plaintext", "ground_station:\n  base_url: ftp://station", "!!python/object:os.system {}"]:
            file.write_text(text, encoding="utf-8")
            with self.assertRaises(Exception):
                load_config(file)

    def test_endpoint_change_does_not_reuse_another_stations_request(self):
        with patch("integrations.groundstation_http.httpx.post", side_effect=httpx.ReadTimeout("lost")):
            with self.assertRaises(ValueError):
                self.client.call("create_task", {"task": TASK})
        previous = json.loads(next((self.directory / "requests").glob("*.json")).read_text(encoding="utf-8"))
        other = GroundStationHTTP(GroundStationConfig(base_url="http://another-station:18003"), self.directory / "requests")
        with patch("integrations.groundstation_http.httpx.post", side_effect=httpx.ReadTimeout("lost")) as send:
            with self.assertRaises(ValueError):
                other.call("create_task", {"task": TASK})
        self.assertNotEqual(send.call_args.kwargs["json"]["header"]["msg_id"], previous["envelope"]["header"]["msg_id"])


if __name__ == "__main__":
    unittest.main()
