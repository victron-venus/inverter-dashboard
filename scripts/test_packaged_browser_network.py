"""Real loopback transport controls; no Docker, equipment or release claims."""

import hashlib
import json
import socket
import socketserver
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import packaged_browser_network as network
from packaged_browser_acceptance import cleanup_runtime, fetch

OWNER = "browser-qa-" + "a" * 16


def fixture_identity(role="fixtures"):
    name = OWNER + "-" + role
    net = {
        "Name": OWNER,
        "Id": "b" * 64,
        "Driver": "bridge",
        "Internal": True,
        "EnableIPv6": False,
        "Labels": {network.OWNER_LABEL: OWNER},
        "Options": {},
        "IPAM": {"Config": [{"Subnet": "172.28.0.0/16", "Gateway": "172.28.0.1"}]},
        "Containers": {
            "c" * 64: {
                "Name": name,
                "EndpointID": "d" * 64,
                "IPv4Address": "172.28.0.2/16",
                "IPv6Address": "",
            }
        },
    }
    container = {
        "Id": "c" * 64,
        "Name": "/" + name,
        "Config": {"Labels": {network.OWNER_LABEL: OWNER}},
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": OWNER, "PortBindings": {}, "PublishAllPorts": False},
        "NetworkSettings": {
            "Ports": {"8090/tcp": None},
            "Networks": {
                OWNER: {
                    "NetworkID": "b" * 64,
                    "EndpointID": "d" * 64,
                    "IPAddress": "172.28.0.2",
                    "Gateway": "172.28.0.1",
                    "GlobalIPv6Address": "",
                }
            },
        },
    }
    return net, container, name


@contextmanager
def running_server(server):
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class HTTPFixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.headers.get("Upgrade") == "websocket":
            key = self.headers["Sec-WebSocket-Key"]
            import base64

            accepted = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
                    usedforsecurity=False,
                ).digest()
            ).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accepted)
            self.end_headers()
            header = self.rfile.read(2)
            length = header[1] & 127
            mask = self.rfile.read(4)
            raw = self.rfile.read(length)
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(raw))
            self.wfile.write(bytes([0x81, len(payload)]) + payload)
            return
        payload = json.dumps({"path": self.path, "host": self.headers["Host"]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class EchoFixture(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        while True:
            data = self.request.recv(8192)
            if not data:
                return
            self.request.sendall(data)


class EndpointTests(unittest.TestCase):
    def test_reciprocal_owned_fixed_service_endpoints(self):
        for role, port in (("fixtures", 8090), ("mqtt", 8080), ("igw", 8080)):
            net, container, name = fixture_identity(role)
            result = network.internal_endpoint(net, container, name, port)
            self.assertEqual((result["address"], result["port"]), ("172.28.0.2", port))
            self.assertTrue(result["internal"])
            self.assertFalse(result["publishedPorts"])

    def test_foreign_network_running_state_or_published_ports_rejected(self):
        changes = [
            lambda n, c: n.update(Internal=False),
            lambda n, c: n.update(Driver="host"),
            lambda n, c: n.update(Labels={}),
            lambda n, c: c["Config"].update(Labels={}),
            lambda n, c: c["State"].update(Running=False),
            lambda n, c: c["HostConfig"].update(NetworkMode="bridge"),
            lambda n, c: c["NetworkSettings"]["Networks"].update(other={}),
            lambda n, c: c["HostConfig"].update(PortBindings={"8090/tcp": [{}]}),
            lambda n, c: c["NetworkSettings"].update(Ports={"8090/tcp": [{}]}),
            lambda n, c: c["NetworkSettings"]["Networks"][OWNER].update(NetworkID="e" * 64),
            lambda n, c: n["Containers"]["c" * 64].update(EndpointID="e" * 64),
            lambda n, c: n["Containers"]["c" * 64].update(Name="foreign"),
        ]
        for change in changes:
            net, container, name = fixture_identity()
            change(net, container)
            with self.assertRaises(ValueError):
                network.internal_endpoint(net, container, name, 8090)

    def test_changed_or_nonprivate_addresses_and_arbitrary_ports_rejected(self):
        for address in ("127.0.0.1", "169.254.169.254", "8.8.8.8", "172.29.0.2", "172.28.0.1"):
            net, container, name = fixture_identity()
            container["NetworkSettings"]["Networks"][OWNER]["IPAddress"] = address
            with self.assertRaises(ValueError):
                network.internal_endpoint(net, container, name, 8090)
        net, container, name = fixture_identity()
        for port in (22, 443, 8080, 65536):
            with self.assertRaises(ValueError):
                network.internal_endpoint(net, container, name, port)

    def test_inspection_uses_exact_network_and_container(self):
        net, container, name = fixture_identity()
        call = Mock(side_effect=[json.dumps([net]), json.dumps([container])])
        network.inspect_endpoint(call, OWNER, name, 8090)
        self.assertEqual(call.call_args_list[0].args, ("docker", "network", "inspect", OWNER))
        self.assertEqual(call.call_args_list[1].args, ("docker", "container", "inspect", name))

    def test_status_diagnostics_do_not_emit_error_or_health_contents(self):
        state = {"Running": False, "ExitCode": 7, "Error": "PRIVATE", "Health": {"Log": "PRIVATE"}}
        result = network.container_status(Mock(return_value=json.dumps(state)), OWNER + "-fixtures")
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(result["exitCode"], 7)


class RelayTests(unittest.TestCase):
    def test_http_and_websocket_upgrade_pass_through_real_loopback_relay(self):
        with running_server(ThreadingHTTPServer(("127.0.0.1", 0), HTTPFixture)) as server:
            relay = network.LoopbackRelay(*server.server_address)
            self.addCleanup(relay.close)
            reply = json.loads(fetch(relay.base, "/probe", credential=""))
            self.assertEqual(reply, {"path": "/probe", "host": relay.base.removeprefix("http://")})
            port = int(relay.base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                stream = client.makefile("rb")
                self.addCleanup(stream.close)
                client.sendall(
                    b"GET /ws HTTP/1.1\r\nHost: loopback\r\nUpgrade: websocket\r\n"
                    b"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
                )
                self.assertIn(b"101", stream.readline())
                for _ in range(32):
                    line = stream.readline()
                    self.assertTrue(line)
                    if line == b"\r\n":
                        break
                else:
                    self.fail("Oversized fixture upgrade response")
                payload, mask = b"real-ws-through-relay", b"mask"
                frame = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                client.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + frame)
                self.assertEqual(stream.read(2), bytes([0x81, len(payload)]))
                self.assertEqual(stream.read(len(payload)), payload)

    def test_large_bidirectional_stream_survives_small_bounded_buffers(self):
        with (
            running_server(
                socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoFixture)
            ) as server,
            patch.object(network, "MAX_BUFFER", 4096),
        ):
            relay = network.LoopbackRelay(*server.server_address)
            self.addCleanup(relay.close)
            port = int(relay.base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                data = b"bounded duplex " * 20000
                sender = threading.Thread(target=client.sendall, args=(data,))
                sender.start()
                got = bytearray()
                while len(got) < len(data):
                    got.extend(client.recv(8192))
                sender.join(timeout=2)
                self.assertFalse(sender.is_alive())
                self.assertEqual(bytes(got), data)
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b"")
            relay.close()

    def test_closing_active_relay_joins_workers_and_refuses_new_connections(self):
        with running_server(
            socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoFixture)
        ) as server:
            relay = network.LoopbackRelay(*server.server_address)
            port = int(relay.base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                client.sendall(b"active")
                self.assertEqual(client.recv(6), b"active")
                relay.close()
                self.assertEqual(client.recv(1), b"")
            self.assertFalse(relay.thread.is_alive())
            self.assertFalse(any(worker.is_alive() for worker in relay.workers))
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.2)

    def test_active_connection_limit_rejects_additional_clients(self):
        with (
            running_server(
                socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoFixture)
            ) as server,
            patch.object(network, "MAX_CONNECTIONS", 1),
        ):
            relay = network.LoopbackRelay(*server.server_address)
            self.addCleanup(relay.close)
            port = int(relay.base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=2) as first:
                first.sendall(b"held")
                self.assertEqual(first.recv(4), b"held")
                with socket.create_connection(("127.0.0.1", port), timeout=2) as second:
                    self.assertEqual(second.recv(1), b"")

    def test_idle_connection_deadline_closes_both_sides(self):
        with (
            running_server(
                socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoFixture)
            ) as server,
            patch.object(network, "IDLE_TIMEOUT", 0.05),
        ):
            relay = network.LoopbackRelay(*server.server_address)
            self.addCleanup(relay.close)
            port = int(relay.base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                self.assertEqual(client.recv(1), b"")

    def test_relay_cleanup_failures_still_attempt_other_relays_and_docker(self):
        first, second = Mock(), Mock()
        second.close.side_effect = RuntimeError("cannot close")
        with (
            patch("packaged_browser_acceptance.cleanup_owned") as docker_cleanup,
            self.assertRaises(RuntimeError),
        ):
            cleanup_runtime([first, second], [], OWNER, OWNER + ":candidate", None, None, "")
        first.close.assert_called_once()
        second.close.assert_called_once()
        docker_cleanup.assert_called_once()

    def test_listener_initialization_failures_always_close_owned_socket(self):
        for operation in ("bind", "listen", "settimeout"):
            listener = Mock()
            getattr(listener, operation).side_effect = OSError("injected setup failure")
            with (
                patch.object(network.socket, "socket", return_value=listener),
                self.assertRaises(OSError),
            ):
                network.LoopbackRelay("127.0.0.1", 8090)
            listener.close.assert_called_once()

    def test_accept_thread_start_failure_closes_actual_bound_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with (
            patch.object(network.socket, "socket", return_value=listener),
            patch.object(network.threading.Thread, "start", side_effect=RuntimeError("no thread")),
            self.assertRaises(RuntimeError),
        ):
            network.LoopbackRelay("127.0.0.1", 8090)
        self.assertEqual(listener.fileno(), -1)

    def test_worker_start_failure_closes_client_releases_slot_and_fails_cleanup(self):
        relay = network.LoopbackRelay("127.0.0.1", 8090)
        port = int(relay.base.rsplit(":", 1)[1])
        with (
            patch.object(network.threading.Thread, "start", side_effect=RuntimeError("no worker")),
            socket.create_connection(("127.0.0.1", port), timeout=2) as client,
        ):
            self.assertEqual(client.recv(1), b"")
        with self.assertRaises(RuntimeError):
            relay.close()
        self.assertFalse(relay.connections)
        self.assertFalse(relay.workers)
        for _ in range(network.MAX_CONNECTIONS):
            self.assertTrue(relay.slots.acquire(blocking=False))
        self.assertFalse(relay.slots.acquire(blocking=False))


if __name__ == "__main__":
    unittest.main()
