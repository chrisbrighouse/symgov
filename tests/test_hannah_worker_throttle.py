import pathlib
import sys
import unittest
from unittest.mock import patch

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.agent_queue_worker import AgentQueueWorkerConfig, drain_agent_queues


class HannahWorkerThrottleTests(unittest.TestCase):
    def test_global_drain_processes_hannah_once(self):
        calls = []

        def fake_once(agent, config):
            calls.append(agent)
            return {"agent": agent, "processedCount": 1, "errorCount": 0, "processed": [], "errors": []}

        config = AgentQueueWorkerConfig(agents=("hannah",), drain=True, limit=10)
        with patch("symgov_backend.agent_queue_worker.process_agent_queue_once", side_effect=fake_once):
            result = drain_agent_queues(config, max_cycles=50)

        self.assertEqual(calls, ["hannah"])
        self.assertEqual(result["processedCount"], 1)
        self.assertEqual(result["cycleCount"], 1)


if __name__ == "__main__":
    unittest.main()
