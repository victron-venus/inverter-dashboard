"""Opt-in hosted topology proof only; never packaged product acceptance.

Ordinary PR CI enables this on a native Ubuntu Docker host. Unit-test discovery
elsewhere skips it; it never contacts equipment or builds an application image.
"""

import json
import os
import sys
import unittest
import uuid

from packaged_browser_acceptance import (
    FIXTURE_IMAGE,
    call,
    fetch,
    remove_cleanup_resources,
    validate_owned_cleanup,
    verify_local_docker,
)
from packaged_browser_network import OWNER_LABEL, close_relays, start_owned_relay

SERVER = """
import http.server,json,sys
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        data=json.dumps({'identity':sys.argv[2],'path':self.path}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
http.server.ThreadingHTTPServer(('0.0.0.0',int(sys.argv[1])),Handler).serve_forever()
"""


def remove_topology(relays, names, owner):
    validate_owned_cleanup(names, owner, owner + ":candidate")
    try:
        close_relays(relays)
    finally:
        errors = remove_cleanup_resources(names, owner, owner + ":candidate")
        if errors:
            raise RuntimeError("Owned topology teardown failed")


@unittest.skipUnless(
    os.environ.get("PACKAGED_TOPOLOGY_DOCKER") == "1", "Explicit hosted topology job only"
)
class HostedTopologyTests(unittest.TestCase):
    def test_internal_only_http_endpoints_are_reachable_through_owned_host_relays(self):
        self.assertEqual(sys.platform, "linux")
        verify_local_docker()
        owner = "browser-qa-" + uuid.uuid4().hex[:16]
        relays, names = [], []
        try:
            call("docker", "pull", FIXTURE_IMAGE, timeout=180)
            call(
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                OWNER_LABEL + "=" + owner,
                owner,
            )
            for role, port in (("fixtures", 8090), ("mqtt", 8080)):
                name = owner + "-" + role
                names.append(name)
                call(
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    owner,
                    "--label",
                    OWNER_LABEL + "=" + owner,
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=128m",
                    "--cpus=1",
                    "--pids-limit=32",
                    FIXTURE_IMAGE,
                    "python",
                    "-c",
                    SERVER,
                    str(port),
                    role,
                )
                relay, identity = start_owned_relay(call, owner, name, port)
                relays.append(relay)
                self.assertTrue(identity["internal"])
                self.assertFalse(identity["publishedPorts"])
                self.assertEqual(identity["container"], name)
                self.assertEqual(identity["port"], port)
                # Container startup is synchronized by the fixed server's listening
                # socket, read inside that exact container without any network call.
                call(
                    "docker",
                    "exec",
                    name,
                    "python",
                    "-c",
                    "import socket,sys,time\n"
                    "for attempt in range(50):\n"
                    " try:\n"
                    "  s=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=.2);s.close();break\n"
                    " except OSError: time.sleep(.1)\n"
                    "else: raise RuntimeError('Synthetic server not listening')",
                    str(port),
                    timeout=10,
                )
                reply = json.loads(fetch(relay.base, "/topology", credential=""))
                self.assertEqual(reply, {"identity": role, "path": "/topology"})
        finally:
            remove_topology(relays, names, owner)


if __name__ == "__main__":
    unittest.main()
