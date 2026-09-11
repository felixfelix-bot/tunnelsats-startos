import unittest
import os
import sys
import json
import io
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import bridge


class TestEgressStatusField(unittest.TestCase):
    """Gate 1 (TDD): failing tests FIRST asserting the new egress status field.

    These tests exercise the pure classification + status surfacing logic. The
    production network probe (probe_egress_ip via curl api.ipify.org) is kept
    honest and is mocked only at the I/O boundary so the unit tests stay
    hermetic — the classification/state logic itself is what's under test.
    """

    def setUp(self):
        bridge._egress_probe_cache = None

    def tearDown(self):
        bridge._egress_probe_cache = None

    # --- get_status() exposes a dedicated egress field ---
    @patch('bridge.is_enabled', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('bridge.get_subscription_info')
    @patch('bridge.get_egress_status')
    def test_get_status_includes_egress_field(
        self, mock_egress, mock_sub, mock_exists, mock_enabled
    ):
        mock_sub.return_value = {
            "linked": True,
            "expiresAt": "2026-12-31T23:59:59Z",
            "formatted": "Active (Expires in 130d)",
            "isExpired": False,
            "daysRemaining": 130,
            "lastSync": None,
            "syncError": None,
            "syncSuccess": True,
        }
        mock_egress.return_value = {
            "state": "tunneled",
            "actual_ip": "1.2.3.4",
            "expected_ip": "1.2.3.4",
            "checked_at": "2026-09-04T12:00:00Z",
            "message": "Outbound traffic is tunneled via TunnelSats.",
        }

        status = bridge.get_status()

        self.assertIn("egress", status)
        self.assertEqual(status["egress"]["state"], "tunneled")
        self.assertEqual(status["egress"]["actual_ip"], "1.2.3.4")
        self.assertTrue(status["egress_tunneled"])

    # --- classification: confirmed mismatch -> 'outbound not tunneled' ---
    def test_compute_egress_state_not_tunneled_on_mismatch(self):
        state = bridge.compute_egress_state(
            actual_ip="203.0.113.9",
            expected_raw="ch1.tunnelsats.com",
            expected_resolved="1.2.3.4",
        )
        self.assertEqual(state, "not_tunneled")

    def test_compute_egress_state_tunneled_on_match(self):
        state = bridge.compute_egress_state(
            actual_ip="1.2.3.4",
            expected_raw="ch1.tunnelsats.com",
            expected_resolved="1.2.3.4",
        )
        self.assertEqual(state, "tunneled")

    def test_compute_egress_state_tunneled_when_actual_matches_raw_host(self):
        # Endpoint may be a literal IP; actual egress equals it -> tunneled.
        state = bridge.compute_egress_state(
            actual_ip="9.9.9.9",
            expected_raw="9.9.9.9",
            expected_resolved=None,
        )
        self.assertEqual(state, "tunneled")

    def test_compute_egress_state_unverified_when_no_expected_ip(self):
        state = bridge.compute_egress_state(
            actual_ip="203.0.113.9",
            expected_raw=None,
            expected_resolved=None,
        )
        self.assertEqual(state, "unverified")

    def test_compute_egress_state_unverified_when_no_actual_ip(self):
        state = bridge.compute_egress_state(
            actual_ip=None,
            expected_raw="ch1.tunnelsats.com",
            expected_resolved="1.2.3.4",
        )
        self.assertEqual(state, "unverified")

    # --- get_status reflects a not_tunneled warning ---
    @patch('bridge.is_enabled', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('bridge.get_subscription_info')
    @patch('bridge.get_egress_status')
    def test_get_status_not_tunneled_sets_explicit_warning_state(
        self, mock_egress, mock_sub, mock_exists, mock_enabled
    ):
        mock_sub.return_value = {
            "linked": True,
            "expiresAt": "2026-12-31T23:59:59Z",
            "formatted": "Active (Expires in 130d)",
            "isExpired": False,
            "daysRemaining": 130,
            "lastSync": None,
            "syncError": None,
            "syncSuccess": True,
        }
        mock_egress.return_value = {
            "state": "not_tunneled",
            "actual_ip": "203.0.113.9",
            "expected_ip": "1.2.3.4",
            "checked_at": "2026-09-04T12:00:00Z",
            "message": (
                "Outbound traffic is NOT tunneled via TunnelSats: the tunnel "
                "is fail-open. Verify the host-managed gateway in "
                "System > Gateways."
            ),
        }

        status = bridge.get_status()

        self.assertIn("egress", status)
        self.assertEqual(status["egress"]["state"], "not_tunneled")
        self.assertFalse(status["egress_tunneled"])

    # --- get_egress_status returns cached probe without re-probing ---
    def test_get_egress_status_returns_cached_probe(self):
        bridge._egress_probe_cache = {
            "state": "tunneled",
            "actual_ip": "1.2.3.4",
            "expected_ip": "1.2.3.4",
            "checked_at": "2026-09-04T12:00:00Z",
        }
        with patch('bridge.run_egress_probe') as mock_run:
            status = bridge.get_egress_status()
            mock_run.assert_not_called()
        self.assertEqual(status["state"], "tunneled")
        self.assertEqual(status["expected_ip"], "1.2.3.4")

    # --- health egress command fails closed when not tunneled ---
    @patch('bridge.is_enabled', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('bridge.run_egress_probe')
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_health_egress_not_tunneled_fails_closed(
        self, mock_stdout, mock_run, mock_exists, mock_enabled
    ):
        mock_run.return_value = {
            "state": "not_tunneled",
            "actual_ip": "203.0.113.9",
            "expected_ip": "1.2.3.4",
            "checked_at": "2026-09-04T12:00:00Z",
            "message": (
                "Outbound traffic is NOT tunneled via TunnelSats: the tunnel "
                "is fail-open. Verify the host-managed gateway in "
                "System > Gateways."
            ),
        }
        with patch('sys.argv', ['bridge.py', 'health', 'egress']):
            with self.assertRaises(SystemExit) as cm:
                bridge.main()
            self.assertEqual(cm.exception.code, 1)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["result"], "failure")
        self.assertIn("not tunneled", output["message"].lower())

    @patch('bridge.is_enabled', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('bridge.run_egress_probe')
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_health_egress_tunneled_ok(
        self, mock_stdout, mock_run, mock_exists, mock_enabled
    ):
        mock_run.return_value = {
            "state": "tunneled",
            "actual_ip": "1.2.3.4",
            "expected_ip": "1.2.3.4",
            "checked_at": "2026-09-04T12:00:00Z",
            "message": "Outbound traffic is tunneled via TunnelSats.",
        }
        with patch('sys.argv', ['bridge.py', 'health', 'egress']):
            with self.assertRaises(SystemExit) as cm:
                bridge.main()
            self.assertEqual(cm.exception.code, 0)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["result"], "ok")
        self.assertIn("tunneled", output["message"].lower())


class TestExpectedTunnelIp(unittest.TestCase):
    """Parsing of the tunnel server IP from the WireGuard Endpoint."""

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_get_expected_tunnel_ip_parses_endpoint(self, mock_open, mock_exists):
        conf = "[Interface]\nAddress = 10.9.0.102/32\n# VPNPort: 24556\n[Peer]\nEndpoint = ch1.tunnelsats.com:51820\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname', return_value="1.2.3.4"):
            result = bridge.get_expected_tunnel_ip()
        self.assertEqual(result["raw_host"], "ch1.tunnelsats.com")
        self.assertEqual(result["resolved_ip"], "1.2.3.4")
        self.assertTrue(result["is_hostname"])

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_get_expected_tunnel_ip_accepts_literal_ip_endpoint(
        self, mock_open, mock_exists
    ):
        conf = "[Interface]\nAddress = 10.9.0.102/32\n# VPNPort: 24556\n[Peer]\nEndpoint = 9.9.9.9:51820\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname') as mock_resolve:
            result = bridge.get_expected_tunnel_ip()
            mock_resolve.assert_not_called()
        self.assertEqual(result["raw_host"], "9.9.9.9")
        self.assertEqual(result["resolved_ip"], "9.9.9.9")
        self.assertFalse(result["is_hostname"])

    @patch('os.path.exists', return_value=False)
    def test_get_expected_tunnel_ip_unconfigured(self, mock_exists):
        result = bridge.get_expected_tunnel_ip()
        self.assertEqual(result["raw_host"], None)
        self.assertEqual(result["resolved_ip"], None)

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_get_expected_tunnel_ip_parses_bracketed_ipv6_endpoint(
        self, mock_open, mock_exists
    ):
        # A bracketed IPv6 literal Endpoint must unwrap cleanly, never yield a
        # malformed capture ('[2001' from a naive colon-stopping regex).
        conf = "[Interface]\n[Peer]\nEndpoint = [2001:db8::1]:51820\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname') as mock_resolve:
            result = bridge.get_expected_tunnel_ip()
            mock_resolve.assert_not_called()
        self.assertEqual(result["raw_host"], "2001:db8::1")
        self.assertEqual(result["resolved_ip"], "2001:db8::1")
        self.assertFalse(result["is_hostname"])

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_get_expected_tunnel_ip_dns_failure_is_unverified(
        self, mock_open, mock_exists
    ):
        # If the Endpoint hostname cannot be resolved, resolved_ip stays None;
        # the state machine maps this to 'unverified', never to a false
        # 'tunneled' claim.
        conf = "[Interface]\n[Peer]\nEndpoint = ch1.tunnelsats.com:51820\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname', side_effect=Exception("NXDOMAIN")):
            result = bridge.get_expected_tunnel_ip()
        self.assertEqual(result["raw_host"], "ch1.tunnelsats.com")
        self.assertEqual(result["resolved_ip"], None)
        self.assertTrue(result["is_hostname"])

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_get_expected_tunnel_ip_unwraps_host_port(self, mock_open, mock_exists):
        # A bare hostname+port Endpoint (no brackets) must drop the port.
        conf = "[Interface]\n[Peer]\nEndpoint = ch1.tunnelsats.com:51820\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname', return_value="1.2.3.4"):
            result = bridge.get_expected_tunnel_ip()
        self.assertEqual(result["raw_host"], "ch1.tunnelsats.com")
        self.assertEqual(result["resolved_ip"], "1.2.3.4")
        self.assertTrue(result["is_hostname"])


class TestEgressColdReviewFixes(unittest.TestCase):
    """Regressions for the two blocking findings of the cold cross-family
    review of the final diff (2026-09-11):

    1. A hostname Endpoint whose DNS lookup fails must classify as
       'unverified' — never as a false 'not_tunneled' alarm.
    2. get_egress_status() must never block on the network for a non-forced
       read (the /api/status + dashboard path), even before the first
       background probe has produced a result.
    """

    def setUp(self):
        bridge._egress_probe_cache = None

    def tearDown(self):
        bridge._egress_probe_cache = None

    def test_hostname_dns_failure_classifies_unverified(self):
        # Endpoint = ch1.tunnelsats.com failed to resolve (resolved_ip=None):
        # the outbound IP cannot be proven tunneled, but it also cannot be
        # proven untunneled. Honest answer is 'unverified' (which the health
        # command still treats as failure — fail-closed).
        state = bridge.compute_egress_state(
            "203.0.113.99", "ch1.tunnelsats.com", None
        )
        self.assertEqual(state, "unverified")

    def test_literal_ip_mismatch_still_classifies_not_tunneled(self):
        # A literal-IP Endpoint that does not match the observed egress is a
        # confirmed mismatch -> explicit warning state (guards the fix against
        # over-classifying every mismatch as 'unverified').
        state = bridge.compute_egress_state(
            "203.0.113.99", "198.51.100.10", "198.51.100.10"
        )
        self.assertEqual(state, "not_tunneled")

    def test_non_forced_status_never_blocks_before_first_probe(self):
        # The web/API read path must be non-blocking: with no cached result
        # yet, it returns an honest 'unverified' placeholder instead of
        # synchronously running curl (up to ~12s) on the request thread.
        with patch('bridge.probe_egress_ip') as mock_probe, patch(
            'bridge.get_expected_tunnel_ip'
        ) as mock_expected:
            status = bridge.get_egress_status()
            mock_probe.assert_not_called()
            mock_expected.assert_not_called()
        self.assertEqual(status["state"], "unverified")
        self.assertFalse(status["egress_tunneled"] if "egress_tunneled" in status
                         else bridge.compute_egress_state(
                             status.get("actual_ip"), None, None) == "tunneled")

    def test_get_status_does_not_block_on_cold_cache(self):
        with patch('bridge.is_enabled', return_value=True), patch(
            'os.path.exists', return_value=True
        ), patch('bridge.get_subscription_info') as mock_sub, patch(
            'bridge.probe_egress_ip'
        ) as mock_probe:
            mock_sub.return_value = {
                "linked": True,
                "isExpired": False,
                "expiresAt": "2026-12-31",
                "daysRemaining": 100,
                "formatted": "2026-12-31",
                "lastSync": "2026-09-11T00:00:00Z",
                "syncError": None,
            }
            status = bridge.get_status()
            mock_probe.assert_not_called()
        self.assertIn("egress", status)
        self.assertEqual(status["egress"]["state"], "unverified")
        self.assertFalse(status["egress_tunneled"])

    @patch('bridge.is_enabled', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('bridge.run_egress_probe')
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_health_egress_unverified_fails_closed(
        self, mock_stdout, mock_run, mock_exists, mock_enabled
    ):
        # Pins the central fail-closed claim: an indeterminate probe result
        # must be a failure, never a silent 'ok'.
        mock_run.return_value = {
            "state": "unverified",
            "actual_ip": None,
            "expected_ip": "ch1.tunnelsats.com",
            "checked_at": "2026-09-11T12:00:00Z",
            "message": bridge.EGRESS_UNVERIFIED_MSG,
        }
        with patch('sys.argv', ['bridge.py', 'health', 'egress']):
            with self.assertRaises(SystemExit) as cm:
                bridge.main()
            self.assertEqual(cm.exception.code, 1)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["result"], "failure")

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_malformed_bracketed_endpoint_is_unverified(
        self, mock_open, mock_exists
    ):
        # A truncated bracket ('[abc') must never be sliced into a garbled
        # host that could be compared against the real egress IP.
        conf = "[Interface]\n[Peer]\nEndpoint = [abc\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname') as mock_resolve:
            result = bridge.get_expected_tunnel_ip()
            mock_resolve.assert_not_called()
        self.assertIsNone(result["raw_host"])
        self.assertIsNone(result["resolved_ip"])
        self.assertEqual(
            bridge.compute_egress_state("203.0.113.9", result["raw_host"],
                                        result["resolved_ip"]),
            "unverified",
        )

    def test_probe_forces_ipv4(self):
        # api.ipify.org is dual-stack while this package routes IPv4 only: the
        # probe must force IPv4, otherwise a dual-stack host can receive an
        # IPv6 answer that socket.inet_aton rejects and be misreported as
        # 'unverified' even when the IPv4 egress is correctly tunneled.
        completed = unittest.mock.MagicMock(returncode=0, stdout="198.51.100.10")
        with patch('bridge.subprocess.run', return_value=completed) as mock_run:
            ip = bridge.probe_egress_ip()
        self.assertEqual(ip, "198.51.100.10")
        argv = mock_run.call_args[0][0]
        self.assertIn("-4", argv)
        self.assertIn("https://api.ipify.org", argv)

    def test_probe_rejects_non_ipv4_output(self):
        # Defensive: a non-IPv4 body must never be returned as an egress IP.
        completed = unittest.mock.MagicMock(returncode=0, stdout="2001:db8::1\n")
        with patch('bridge.subprocess.run', return_value=completed):
            self.assertIsNone(bridge.probe_egress_ip())

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open')
    def test_bare_ipv6_literal_endpoint_is_not_mangled(
        self, mock_open, mock_exists
    ):
        # An unbracketed IPv6 literal Endpoint contains ':' but has no port;
        # naive colon-splitting would truncate it to '2001' and poison the
        # comparison.
        conf = "[Interface]\n[Peer]\nEndpoint = 2001:db8::1\n"
        mock_open.return_value = unittest.mock.mock_open(read_data=conf)()
        with patch('socket.gethostbyname') as mock_resolve:
            result = bridge.get_expected_tunnel_ip()
            mock_resolve.assert_not_called()
        self.assertEqual(result["raw_host"], "2001:db8::1")
        self.assertEqual(result["resolved_ip"], "2001:db8::1")
        self.assertFalse(result["is_hostname"])


if __name__ == '__main__':
    unittest.main()
