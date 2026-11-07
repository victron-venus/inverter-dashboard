"""Opt-in hosted topology proof only; never packaged product acceptance.

Ordinary PR CI enables this on a native Ubuntu Docker host. Unit-test discovery
elsewhere skips it; it never contacts equipment or builds an application image.
"""

import json
import os
import secrets
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from packaged_browser_acceptance import (
    FIXTURE_IMAGE,
    call,
    config_files,
    fetch,
    remove_cleanup_resources,
    start_fixture,
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

TLS_PROBE = """
import http.client,json,os,socket,ssl,stat
key=os.stat('/qa-config/fixture.key')
cert=os.stat('/qa-config/fixture.crt')
context=ssl.create_default_context(cafile='/qa-config/fixture.crt')
def snapshot(ctx,hostname):
    with socket.create_connection(('127.0.0.1',8443),timeout=2) as raw:
        with ctx.wrap_socket(raw,server_hostname=hostname) as connection:
            token=os.environ['CI_PACKAGED_BROWSER_TOKEN']
            request='GET /v1/snapshot HTTP/1.1\\r\\nHost: fixtures\\r\\nAuthorization: Bearer '+token+'\\r\\nConnection: close\\r\\n\\r\\n'
            connection.sendall(request.encode())
            response=http.client.HTTPResponse(connection)
            response.begin()
            data=response.read(65537)
            if response.status!=200 or len(data)>65536:
                raise RuntimeError('Synthetic TLS GET failed')
            if json.loads(data)['inverter']['version']!='packaged-fixture':
                raise RuntimeError('Unexpected synthetic snapshot')
snapshot(context,'fixtures')
rejections=0
for ctx,hostname in ((context,'wrong-fixture.invalid'),(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),'fixtures')):
    try:
        snapshot(ctx,hostname)
    except ssl.SSLCertVerificationError:
        rejections+=1
    else:
        raise RuntimeError('Invalid fixture TLS identity accepted')
with open('/proc/self/status') as status:
    capabilities=next(line.split()[1] for line in status if line.startswith('CapEff:'))
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'keyOwner':key.st_uid,
    'keyMode':stat.S_IMODE(key.st_mode),'publicCertReadable':stat.S_IMODE(cert.st_mode)&0o444==0o444,
    'effectiveCapabilities':int(capabilities,16),'tlsVerified':context.verify_mode==ssl.CERT_REQUIRED,
    'hostnameChecked':context.check_hostname,'invalidTLSRejected':rejections,'snapshot':'packaged-fixture'}))
"""


def wait_for_port(name, port):
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
    def relay(self, owner, name, port, relays):
        wait_for_port(name, port)
        relay, identity = start_owned_relay(call, owner, name, port)
        relays.append(relay)
        self.assertTrue(identity["internal"])
        self.assertFalse(identity["publishedPorts"])
        self.assertEqual(identity["container"], name)
        self.assertEqual(identity["port"], port)
        return relay

    def test_internal_only_http_endpoints_and_owner_only_fixture_tls_startup(self):
        self.assertEqual(sys.platform, "linux")
        verify_local_docker()
        owner = "browser-qa-" + uuid.uuid4().hex[:16]
        relays, names = [], []
        with tempfile.TemporaryDirectory(prefix="packaged-topology-") as temporary:
            config = Path(temporary).resolve()
            config.chmod(0o755)
            credential = secrets.token_urlsafe(32)
            config_files(config, credential)
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
                fixture_name = owner + "-fixtures"
                names.append(fixture_name)
                start_fixture(
                    Path(__file__).resolve().parents[1], config, fixture_name, owner, credential
                )
                relay = self.relay(owner, fixture_name, 8090, relays)
                status = json.loads(fetch(relay.base, "/fixture/status", credential=credential))
                self.assertEqual(status["capture"], [])
                self.assertGreater(status["eventEpoch"], 0)
                proof = json.loads(
                    call("docker", "exec", fixture_name, "python", "-c", TLS_PROBE, timeout=15)
                )
                self.assertEqual(
                    proof,
                    {
                        "uid": os.getuid(),
                        "gid": os.getgid(),
                        "keyOwner": os.getuid(),
                        "keyMode": 0o600,
                        "publicCertReadable": True,
                        "effectiveCapabilities": 0,
                        "tlsVerified": True,
                        "hostnameChecked": True,
                        "invalidTLSRejected": 2,
                        "snapshot": "packaged-fixture",
                    },
                )
                print(
                    json.dumps(
                        {"scope": "synthetic-topology-and-TLS-only", **proof}, sort_keys=True
                    )
                )
                name = owner + "-mqtt"
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
                    "8080",
                    "mqtt",
                )
                app_relay = self.relay(owner, name, 8080, relays)
                reply = json.loads(fetch(app_relay.base, "/topology", credential=""))
                self.assertEqual(reply, {"identity": "mqtt", "path": "/topology"})
            finally:
                remove_topology(relays, names, owner)


if __name__ == "__main__":
    unittest.main()
