"""Remote inverter-gateway (IGW) client — mirrors inverter-desktop gateway.rs.

Live Cerbo tiles come from either:
- Cerbo/LAN MQTT (``MQTT_HOST``), or
- remote inverter-gateway (``GATEWAY_ENABLED`` + ``GATEWAY_URL`` → ``GET /v1/snapshot``).

Precedence (same idea as inverter-desktop ``connectionPolicy``):
1. Only ``MQTT_HOST`` → Cerbo MQTT client.
2. Only IGW → snapshot poller.
3. Both configured → probe MQTT; if the broker accepts TCP, use MQTT,
   otherwise use IGW. Dual-path mode may fail over MQTT→IGW and later
   recover IGW→MQTT when the broker is reachable again.
4. Neither → no live data source.

mp production clears ``MQTT_HOST`` so IGW stays primary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx

from . import config
from .cerbo import INVERTER_STATES, state_from_current

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 2.0
REQUEST_TIMEOUT_SECS = 25.0
MQTT_PROBE_TIMEOUT_SECS = 3.0
MQTT_CONNECT_WATCHDOG_SECS = 15.0
MQTT_RECOVERY_PROBE_SECS = 60.0

StartupSource = Literal["mqtt", "igw", "none"]

# Exclusive live path selected at startup (and updated on dual-path failover).
_active_source: StartupSource = "none"
_dual_path: bool = False


def gateway_configured() -> bool:
    """True when remote IGW looks configured (enabled + URL)."""
    return bool(config.GATEWAY_ENABLED and (config.GATEWAY_URL or "").strip())


def mqtt_configured() -> bool:
    """True when a Cerbo/LAN MQTT host is configured."""
    return bool((config.MQTT_HOST or "").strip())


def set_active_source(source: StartupSource, *, dual_path: bool = False) -> None:
    """Record the exclusive live path (commands/ack follow this)."""
    global _active_source, _dual_path
    _active_source = source
    _dual_path = dual_path and source in ("mqtt", "igw")


def active_source() -> StartupSource:
    """Currently selected exclusive data source."""
    return _active_source


def dual_path_enabled() -> bool:
    """True when both MQTT and IGW were configured at selection time."""
    return _dual_path


def prefer_gateway() -> bool:
    """True when the live exclusive path is IGW (commands/ack via gateway).

    After startup selection this follows ``active_source()``. Before selection
    (tests / early imports) it is True only for IGW-only configs so a coexisting
    ``MQTT_HOST`` is not abandoned by default.
    """
    if _active_source != "none":
        return _active_source == "igw"
    return gateway_configured() and not mqtt_configured()


def choose_startup_source(
    *,
    mqtt_configured: bool,
    igw_configured: bool,
    mqtt_reachable: bool,
) -> StartupSource:
    """Choose the exclusive live path — mirrors desktop ``chooseStartupSource``."""
    if mqtt_configured and igw_configured:
        return "mqtt" if mqtt_reachable else "igw"
    if mqtt_configured:
        return "mqtt"
    if igw_configured:
        return "igw"
    return "none"


async def probe_mqtt_reachable(timeout: float = MQTT_PROBE_TIMEOUT_SECS) -> bool:
    """True when ``MQTT_HOST:MQTT_PORT`` accepts a TCP connection."""
    host = (config.MQTT_HOST or "").strip()
    if not host:
        return False
    port = int(config.MQTT_PORT or 1883)
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.debug("MQTT probe %s:%s failed: %s", host, port, e)
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except Exception as close_err:  # pylint: disable=broad-except
        logger.debug("MQTT probe close %s:%s: %s", host, port, close_err)
    logger.debug("MQTT probe %s:%s ok", host, port)
    return True


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _path_num(m: dict[str, Any], path: str) -> float | None:
    return _num(m.get(path)) if m else None


def _path_str(m: dict[str, Any], path: str) -> str | None:
    v = m.get(path) if m else None
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _instances(m: dict[str, Any]) -> list[str]:
    insts: set[str] = set()
    for key in m:
        inst = key.split("/", 1)[0]
        if inst:
            insts.add(inst)
    return sorted(insts, key=lambda x: int(x) if x.isdigit() else 0)


def apply_snapshot(ms: Any, snap: dict[str, Any]) -> None:
    """Map IGW snapshot leaf maps into MqttState device maps + overlays.

    ``ms`` is an MqttState (duck-typed to avoid circular imports).
    """
    system = snap.get("system") or {}
    battery = snap.get("battery") or {}
    solarcharger = snap.get("solarcharger") or {}
    pvinverter = snap.get("pvinverter") or {}
    vebus = snap.get("vebus") or {}
    acload = snap.get("acload") or {}
    tank = snap.get("tank") or {}
    pump = snap.get("pump") or {}
    ev = snap.get("ev") or {}
    evcharger = snap.get("evcharger") or {}

    # --- systemcalc ---
    ms._system.clear()
    sys_entry: dict[str, Any] = {}
    g1 = _path_num(system, "0/Ac/Grid/L1/Power")
    g2 = _path_num(system, "0/Ac/Grid/L2/Power")
    t1 = _path_num(system, "0/Ac/Consumption/L1/Power")
    t2 = _path_num(system, "0/Ac/Consumption/L2/Power")
    if g1 is not None:
        sys_entry["g1"] = g1
    if g2 is not None:
        sys_entry["g2"] = g2
    if t1 is not None:
        sys_entry["t1"] = t1
    if t2 is not None:
        sys_entry["t2"] = t2
    if sys_entry:
        ms._system["0"] = sys_entry

    # --- batteries ---
    ms._batteries.clear()
    for inst in _instances(battery):
        entry: dict[str, Any] = {"instance": inst}
        soc = _path_num(battery, f"{inst}/Soc")
        voltage = _path_num(battery, f"{inst}/Dc/0/Voltage")
        current = _path_num(battery, f"{inst}/Dc/0/Current")
        power = _path_num(battery, f"{inst}/Dc/0/Power")
        name = _path_str(battery, f"{inst}/CustomName") or _path_str(battery, f"{inst}/ProductName")
        serial = _path_str(battery, f"{inst}/Serial")
        if voltage is not None:
            entry["voltage"] = voltage
        if current is not None:
            entry["current"] = current
            entry["state"] = state_from_current(current)
        if power is not None:
            entry["power"] = power
        if soc is not None:
            entry["soc"] = soc
        if name:
            entry["name"] = name
        if serial:
            entry["serial"] = serial
        if len(entry) > 1:
            ms._batteries[inst] = entry

    # --- MPPT ---
    ms._chargers.clear()
    for inst in _instances(solarcharger):
        entry = {}
        power = _path_num(solarcharger, f"{inst}/Yield/Power") or _path_num(
            solarcharger, f"{inst}/Dc/0/Power"
        )
        current = _path_num(solarcharger, f"{inst}/Dc/0/Current")
        pv_v = _path_num(solarcharger, f"{inst}/Pv/V")
        name = _path_str(solarcharger, f"{inst}/CustomName") or _path_str(
            solarcharger, f"{inst}/ProductName"
        )
        serial = _path_str(solarcharger, f"{inst}/Serial")
        if power is not None:
            entry["power"] = power
        if current is not None:
            entry["current"] = current
        if pv_v is not None:
            entry["pv_voltage"] = pv_v
        if name:
            entry["name"] = name
        if serial:
            entry["serial"] = serial
        if entry:
            ms._chargers[inst] = entry

    # --- AC PV inverters ---
    ms._pv_inverters.clear()
    for inst in _instances(pvinverter):
        power = _path_num(pvinverter, f"{inst}/Ac/Power") or _path_num(
            pvinverter, f"{inst}/Ac/L1/Power"
        )
        name = _path_str(pvinverter, f"{inst}/CustomName") or _path_str(
            pvinverter, f"{inst}/ProductName"
        )
        serial = _path_str(pvinverter, f"{inst}/Serial")
        if power is None and not name:
            continue
        entry = {"instance": inst}
        if power is not None:
            entry["power"] = power
        if name:
            entry["name"] = name
        if serial:
            entry["serial"] = serial
        ms._pv_inverters[inst] = entry

    # --- VE.Bus ---
    ms._vebus.clear()
    for inst in _instances(vebus):
        entry = {}
        sp = _path_num(vebus, f"{inst}/Hub4/L1/AcPowerSetpoint")
        state_code = _path_num(vebus, f"{inst}/State")
        l1 = _path_num(vebus, f"{inst}/Ac/ActiveIn/L1/Power") or _path_num(
            vebus, f"{inst}/Ac/L1/Power"
        )
        l2 = _path_num(vebus, f"{inst}/Ac/ActiveIn/L2/Power") or _path_num(
            vebus, f"{inst}/Ac/L2/Power"
        )
        ac_p = _path_num(vebus, f"{inst}/Ac/Out/P") or _path_num(vebus, f"{inst}/Ac/Power")
        if sp is not None:
            entry["setpoint"] = sp
        if state_code is not None:
            code = int(state_code)
            entry["inverter_state"] = INVERTER_STATES.get(code, f"? ({code})")
        if l1 is not None:
            entry["l1_power"] = l1
        if l2 is not None:
            entry["l2_power"] = l2
        if ac_p is not None:
            entry["ac_power"] = ac_p
        if entry:
            ms._vebus[inst] = entry

    # --- acload ---
    ms._acload_powers.clear()
    ms._acload_names.clear()
    for inst in _instances(acload):
        power = _path_num(acload, f"{inst}/Ac/Power") or _path_num(acload, f"{inst}/Ac/L1/Power")
        if power is None:
            continue
        ms._acload_powers[inst] = power
        name = _path_str(acload, f"{inst}/CustomName") or _path_str(acload, f"{inst}/ProductName")
        if name:
            ms._acload_names[inst] = name

    # Overlays for grid/battery/solar/loads/setpoint
    ms._apply_cerbo_overlays()

    # --- water (tank Level; pump Status if present — IGW may omit State) ---
    tank_inst = str(config.WATER_TANK_INSTANCE)
    level = _path_num(tank, f"{tank_inst}/Level")
    if level is not None:
        # Victron Level is sometimes 0..1 fraction (desktop normalizes).
        ms.current_state["water_level"] = level * 100.0 if level <= 1.0 else level
    for inst, key in (
        (str(config.WATER_VALVE_INSTANCE), "water_valve"),
        (str(config.WATER_PUMP_INSTANCE), "pump_switch"),
    ):
        status = _path_num(pump, f"{inst}/Status")
        if status is None:
            status = _path_num(pump, f"{inst}/State")
        if status is not None:
            ms.current_state[key] = bool(status)

    # --- EV ---
    ev_inst = str(config.EV_INSTANCE)
    soc = _path_num(ev, f"{ev_inst}/Soc")
    if soc is not None:
        ms.current_state["car_soc"] = soc
    ev_power = _path_num(ev, f"{ev_inst}/Ac/Power")
    if ev_power is not None:
        ms.current_state["ev_power"] = ev_power
    evc_inst = str(config.EVCHARGER_INSTANCE)
    evc_power = _path_num(evcharger, f"{evc_inst}/Ac/Power")
    if evc_power is not None:
        ms.current_state["ev_charging_kw"] = evc_power / 1000.0

    # Alert banners (desktop parity): Venus-platform GUIv2 slots from IGW
    # ``platform`` leaves; Alarms/* fallback when platform never seen.
    platform = snap.get("platform") or {}
    if hasattr(ms, "sync_platform_from_snapshot"):
        ms.sync_platform_from_snapshot(platform if isinstance(platform, dict) else {})
    if hasattr(ms, "sync_alarms_from_snapshot"):
        ms.sync_alarms_from_snapshot(snap)


def build_headers() -> dict[str, str]:
    """CF Access service-token + optional GATEWAY_API_TOKEN bearer."""
    headers = {"User-Agent": "inverter-dashboard/gateway"}
    cid = (config.GATEWAY_ACCESS_CLIENT_ID or "").strip()
    csec = (config.GATEWAY_ACCESS_CLIENT_SECRET or "").strip()
    if cid:
        headers["CF-Access-Client-Id"] = cid
    if csec:
        headers["CF-Access-Client-Secret"] = csec
    token = (config.GATEWAY_API_TOKEN or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_snapshot(client: httpx.AsyncClient) -> dict[str, Any]:
    """GET /v1/snapshot; raises httpx.HTTPStatusError on non-2xx."""
    base = config.GATEWAY_URL.rstrip("/")
    url = f"{base}/v1/snapshot"
    resp = await client.get(url, headers=build_headers())
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise TypeError("gateway snapshot is not a JSON object")
    return data


async def post_command(name: str, body: dict[str, Any] | None = None) -> None:
    """POST /v1/commands/{name} (whitelist only on the gateway)."""
    base = config.GATEWAY_URL.rstrip("/")
    url = f"{base}/v1/commands/{name.strip('/')}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECS) as client:
        resp = await client.post(url, headers=build_headers(), json=body or {})
        resp.raise_for_status()


async def gateway_poll_loop(app_state, mqtt_state_emit) -> None:
    """Background poller: fetch snapshot → apply → emit.

    ``app_state`` is the server AppState duck-type (gateway_* / mqtt_connected).
    ``mqtt_state_emit`` is an awaitable callback after apply (usually ms._emit).
    """
    delay = max(config.GATEWAY_POLL_INTERVAL, 0.5)
    logged_ok = False
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECS) as client:
        while True:
            try:
                snap = await fetch_snapshot(client)
                await mqtt_state_emit(snap)
                app_state.gateway_connected = True
                app_state.mqtt_connected = True
                app_state.gateway_polls += 1
                if not logged_ok:
                    logger.info(
                        "IGW connected to %s (polling /v1/snapshot every %.1fs)",
                        config.GATEWAY_URL.rstrip("/"),
                        delay,
                    )
                    logged_ok = True
            except asyncio.CancelledError:
                logger.info("IGW poller stopped")
                raise
            except Exception as e:  # pylint: disable=broad-except
                app_state.gateway_errors += 1
                was = app_state.gateway_connected
                app_state.gateway_connected = False
                app_state.mqtt_connected = False
                logged_ok = False
                if was:
                    logger.warning("IGW poll failed: %s", e)
                else:
                    logger.debug("IGW poll failed: %s", e)
            await asyncio.sleep(delay)
