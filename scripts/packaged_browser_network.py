"""Bounded loopback access to exact UUID-owned internal Docker endpoints.

The Linux host can reach an internal bridge directly; Docker cannot publish
ports for an exclusively internal container. No second network is introduced.
"""

from __future__ import annotations

import ipaddress
import json
import re
import selectors
import socket
import threading
import time
from contextlib import suppress

OWNER_LABEL = "dashboard.packaged-qa"
DOCKER_ID_PATTERN = r"[a-f0-9]{64}"
MAX_CONNECTIONS = 32
MAX_BUFFER = 65536
CONNECT_TIMEOUT = 5
IDLE_TIMEOUT = 60
CONNECTION_TIMEOUT = 300
PRIVATE_IPV4 = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def internal_endpoint(network, container, name, port):
    """Validate reciprocal ownership without returning environment or mounts."""
    owner = network.get("Name", "")
    require(re.fullmatch(r"browser-qa-[a-f0-9]{16}", owner), "Invalid owned network name")
    require(
        (name == owner + "-fixtures" and port == 8090)
        or (name in {owner + "-mqtt", owner + "-igw"} and port == 8080),
        "Unexpected owned service endpoint",
    )
    network_id = network.get("Id", "")
    container_id = container.get("Id", "")
    require(
        re.fullmatch(DOCKER_ID_PATTERN, network_id)
        and re.fullmatch(DOCKER_ID_PATTERN, container_id),
        "Missing exact Docker resource IDs",
    )
    require(
        network.get("Driver") == "bridge"
        and network.get("Internal") is True
        and network.get("EnableIPv6") is False
        and network.get("Labels", {}).get(OWNER_LABEL) == owner
        and network.get("Options", {}).get("com.docker.network.bridge.gateway_mode_ipv4")
        != "isolated",
        "Owned network must be an internal IPv4 bridge reachable only from its host",
    )
    require(
        container.get("Name") == "/" + name
        and container.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) == owner
        and container.get("State", {}).get("Running") is True
        and container.get("HostConfig", {}).get("NetworkMode") == owner,
        "Container is not the running owned service",
    )
    host = container["HostConfig"]
    settings = container.get("NetworkSettings", {})
    require(
        not host.get("PortBindings")
        and not host.get("PublishAllPorts")
        and not any(settings.get("Ports", {}).values()),
        "Owned containers must not publish ports",
    )
    memberships = settings.get("Networks", {})
    require(set(memberships) == {owner}, "Owned service has additional or missing networks")
    member = memberships[owner]
    reciprocal = network.get("Containers", {}).get(container_id, {})
    require(
        member.get("NetworkID") == network_id
        and re.fullmatch(DOCKER_ID_PATTERN, member.get("EndpointID", ""))
        and member.get("EndpointID") == reciprocal.get("EndpointID")
        and reciprocal.get("Name") == name
        and not member.get("GlobalIPv6Address")
        and not reciprocal.get("IPv6Address"),
        "Owned endpoint is not reciprocally bound to this network",
    )
    address = ipaddress.ip_address(member.get("IPAddress", ""))
    interface = ipaddress.ip_interface(reciprocal.get("IPv4Address", ""))
    pools = network.get("IPAM", {}).get("Config", [])
    require(len(pools) == 1, "Expected one internal IPv4 address pool")
    subnet = ipaddress.ip_network(pools[0].get("Subnet", ""), strict=True)
    require(
        address.version == subnet.version == 4
        and any(subnet.subnet_of(private) for private in PRIVATE_IPV4)
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and interface.ip == address
        and interface.network == subnet
        and address in subnet
        and str(address) not in {pools[0].get("Gateway"), member.get("Gateway")}
        and address not in {subnet.network_address, subnet.broadcast_address},
        "Endpoint address differs from the owned private subnet",
    )
    return {
        "network": owner,
        "networkId": network_id,
        "container": name,
        "containerId": container_id,
        "internal": True,
        "running": True,
        "publishedPorts": False,
        "address": str(address),
        "port": port,
    }


def inspect_endpoint(call, network, name, port):
    networks = json.loads(call("docker", "network", "inspect", network))
    containers = json.loads(call("docker", "container", "inspect", name))
    require(len(networks) == len(containers) == 1, "Ambiguous Docker endpoint inspection")
    require(networks[0].get("Name") == network, "Docker inspected another network")
    return internal_endpoint(networks[0], containers[0], name, port)


def container_status(call, name):
    require(
        re.fullmatch(r"browser-qa-[a-f0-9]{16}-(fixtures|mqtt|igw)", name),
        "Status is limited to owned containers",
    )
    raw = call("docker", "container", "inspect", "--format", "{{json .State}}", name)
    require(len(raw) <= 65536, "Oversized container state")
    state = json.loads(raw)
    # Exclude Error, Health.Log, environment, arguments and all private mounts.
    return {
        "container": name,
        "running": state.get("Running") is True,
        "paused": state.get("Paused") is True,
        "restarting": state.get("Restarting") is True,
        "oomKilled": state.get("OOMKilled") is True,
        "dead": state.get("Dead") is True,
        "exitCode": state.get("ExitCode") if type(state.get("ExitCode")) is int else None,
    }


class Duplex:
    """One finite, buffered, full-duplex stream pair."""

    def __init__(self, client, upstream, stopped):
        self.sockets = (client, upstream)
        self.destinations = {client: upstream, upstream: client}
        self.pending = {client: bytearray(), upstream: bytearray()}
        self.reading = set(self.sockets)
        self.half_closed = set()
        self.started = self.active = time.monotonic()
        self.stopped = stopped
        for stream in self.sockets:
            stream.setblocking(False)

    def events(self, stream):
        opposite = self.destinations[stream]
        events = 0
        if stream in self.reading and len(self.pending[opposite]) < MAX_BUFFER:
            events |= selectors.EVENT_READ
        if self.pending[stream]:
            events |= selectors.EVENT_WRITE
        if (
            opposite not in self.reading
            and not self.pending[stream]
            and stream not in self.half_closed
        ):
            with suppress(OSError):
                stream.shutdown(socket.SHUT_WR)
            self.half_closed.add(stream)
        return events

    def refresh(self, selector):
        for stream in self.sockets:
            events = self.events(stream)
            with suppress(KeyError):
                selector.unregister(stream)
            if events:
                selector.register(stream, events)

    def read(self, stream):
        destination = self.destinations[stream]
        data = stream.recv(MAX_BUFFER - len(self.pending[destination]))
        if data:
            self.pending[destination].extend(data)
            self.active = time.monotonic()
        else:
            self.reading.discard(stream)

    def write(self, stream):
        sent = stream.send(self.pending[stream])
        del self.pending[stream][:sent]
        if sent:
            self.active = time.monotonic()

    def transfer(self, key, events):
        stream = key.fileobj
        with suppress(BlockingIOError):
            if events & selectors.EVENT_READ:
                self.read(stream)
            if events & selectors.EVENT_WRITE:
                self.write(stream)

    def run(self):
        with selectors.DefaultSelector() as selector:
            while not self.stopped.is_set():
                now = time.monotonic()
                if now - self.started > CONNECTION_TIMEOUT or now - self.active > IDLE_TIMEOUT:
                    return
                self.refresh(selector)
                if not self.reading and not any(self.pending.values()):
                    return
                for key, events in selector.select(timeout=0.2):
                    self.transfer(key, events)


class LoopbackRelay:
    """Owned listener; production callers must use start_owned_relay below."""

    def __init__(self, address, port):
        self.endpoint = (address, port)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.workers = []
        self.failures = []
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.thread = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(MAX_CONNECTIONS)
            self.listener.settimeout(0.2)
            self.base = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
            self.thread = threading.Thread(target=self.accept, daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def start_worker(self, client):
        """Caller owns lock and slot; roll back both on thread startup failure."""
        self.connections.add(client)
        try:
            worker = threading.Thread(target=self.forward, args=(client,), daemon=True)
            worker.start()
            self.workers.append(worker)
        except (RuntimeError, OSError) as error:
            self.connections.discard(client)
            client.close()
            self.slots.release()
            self.failures.append(type(error).__name__)
            self.stopped.set()

    def accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError as error:
                if not self.stopped.is_set():
                    self.failures.append(type(error).__name__)
                return
            if not self.slots.acquire(blocking=False):
                client.close()
                continue
            with self.lock:
                if self.stopped.is_set():
                    client.close()
                    self.slots.release()
                    return
                self.workers = [worker for worker in self.workers if worker.is_alive()]
                self.start_worker(client)

    def forward(self, client):
        upstream = None
        try:
            upstream = socket.create_connection(self.endpoint, timeout=CONNECT_TIMEOUT)
            with self.lock:
                self.connections.add(upstream)
            Duplex(client, upstream, self.stopped).run()
        except OSError:
            # Startup refusals and browser disconnects are observable by the
            # caller; application readiness and browser assertions remain strict.
            return
        finally:
            with self.lock:
                for stream in (client, upstream):
                    if stream is not None:
                        self.connections.discard(stream)
                        stream.close()
            self.slots.release()

    def close(self):
        self.stopped.set()
        self.listener.close()
        until = time.monotonic() + CONNECT_TIMEOUT + 2
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(timeout=max(0, until - time.monotonic()))
        with self.lock:
            for stream in self.connections:
                with suppress(OSError):
                    stream.shutdown(socket.SHUT_RDWR)
        for worker in self.workers:
            worker.join(timeout=max(0, until - time.monotonic()))
        active = [worker for worker in [self.thread, *self.workers] if worker is not None]
        if any(worker.is_alive() for worker in active) or self.failures:
            raise RuntimeError("Owned loopback relay cleanup did not complete")


def start_owned_relay(call, network, name, port):
    endpoint = inspect_endpoint(call, network, name, port)
    relay = LoopbackRelay(endpoint["address"], endpoint["port"])
    return relay, {**endpoint, "loopbackOrigin": relay.base}


def close_relays(relays):
    errors = []
    for relay in reversed(relays):
        try:
            relay.close()
        except (OSError, RuntimeError) as error:
            errors.append(type(error).__name__)
    if errors:
        raise RuntimeError("Owned loopback relay cleanup incomplete: " + ",".join(errors))
