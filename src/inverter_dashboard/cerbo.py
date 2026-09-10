"""Cerbo Venus MQTT helpers — live tiles shared with inverter-desktop."""

from __future__ import annotations

import json
from typing import Any

# Live tiles owned by Cerbo MQTT / dbus-* (mirrored out of slim inverter/state).
CERBO_OWNED_KEYS = frozenset(
    {
        "g1",
        "g2",
        "gt",
        "t1",
        "t2",
        "tt",
        "bv",
        "bc",
        "bp",
        "battery_soc",
        "battery_power",
        "battery_voltage",
        "battery_current",
        "batteries",
        "solar_total",
        "pv_total",
        "mppt_total",
        "mppt_data",
        "mppt_individual",
        "mppt_chargers",
        "pv_inverter_total",
        "pv_inverter_individual",
        "pv_inverter_powers",
        "pv_inverters",
        "loads",
        "load_names",
        "setpoint",
        "inverter_state",
        "ev_power",
        "car_soc",
        "ev_charging_kw",
        "water_level",
        "water_valve",
        "pump_switch",
    }
)

INVERTER_STATES = {
    0: "Off",
    1: "Low Power",
    2: "Fault",
    3: "Bulk",
    4: "Absorption",
    5: "Float",
    6: "Storage",
    7: "Equalize",
    8: "Passthru",
    9: "Inverting",
    10: "Power assist",
    11: "Power supply",
    252: "External control",
}

_V_SOC_MIN = 40.0
_V_SOC_MAX = 54.4
KEEPALIVE_INTERVAL_SECS = 45


def voltage_soc(voltage: float) -> float:
    """Map pack voltage to 0–100% SoC (absorption at 54.4 V)."""
    pct = ((voltage - _V_SOC_MIN) / (_V_SOC_MAX - _V_SOC_MIN)) * 100.0
    return round(max(0.0, min(100.0, pct)))


def parse_cerbo_payload(payload: bytes) -> Any:
    """Return the Venus MQTT-GUI ``value`` field, or None on bad payloads."""
    try:
        data = json.loads(payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("value")


def state_from_current(amps: float) -> str:
    if amps > 0.5:
        return "Charging"
    if amps < -0.5:
        return "Discharging"
    return "Idle"


def cerbo_owns_key(
    key: str,
    *,
    has_acloads: bool,
    has_system: bool,
    has_vebus: bool,
    has_batteries: bool,
    has_chargers: bool,
    has_pv: bool,
    has_ev: bool,
    has_water: bool,
) -> bool:
    """True when Cerbo device maps already own this dashboard field."""
    owned = {
        "loads": has_acloads,
        "load_names": has_acloads,
        "g1": has_system or has_vebus,
        "g2": has_system or has_vebus,
        "gt": has_system or has_vebus,
        "t1": has_system or has_vebus,
        "t2": has_system or has_vebus,
        "tt": has_system or has_vebus,
        "battery_soc": has_batteries,
        "battery_power": has_batteries,
        "battery_voltage": has_batteries,
        "battery_current": has_batteries,
        "bv": has_batteries,
        "bc": has_batteries,
        "bp": has_batteries,
        "batteries": has_batteries,
        "solar_total": has_chargers or has_pv,
        "mppt_total": has_chargers or has_pv,
        "mppt_chargers": has_chargers or has_pv,
        "mppt_individual": has_chargers or has_pv,
        "mppt_data": has_chargers or has_pv,
        "pv_total": has_chargers or has_pv,
        "pv_inverters": has_pv,
        "pv_inverter_total": has_pv,
        "pv_inverter_individual": has_pv,
        "pv_inverter_powers": has_pv,
        "setpoint": has_vebus,
        "inverter_state": has_vebus,
        "ev_power": has_ev,
        "car_soc": has_ev,
        "ev_charging_kw": has_ev,
        "water_level": has_water,
        "water_valve": has_water,
        "pump_switch": has_water,
    }
    return owned.get(key, False)


class CerboOverlayMixin:
    """Cerbo device-map overlays for MqttState."""
    def _handle_cerbo_device(self, topic: str, payload: bytes) -> bool:
        """Apply N/<portal>/{system,battery,solarcharger,vebus}/... into device maps."""
        parts = topic.split("/")
        if len(parts) < 5 or parts[0] != "N":
            return False
        kind, instance = parts[2], parts[3]
        if kind not in ("system", "battery", "solarcharger", "vebus"):
            return False
        path = "/".join(parts[4:])
        val = parse_cerbo_payload(payload)
        changed = False
        if kind == "system":
            entry = self._system.setdefault(instance, {})
            if path == "Ac/Grid/L1/Power" and isinstance(val, (int, float)):
                entry["g1"] = float(val)
                changed = True
            elif path == "Ac/Grid/L2/Power" and isinstance(val, (int, float)):
                entry["g2"] = float(val)
                changed = True
            elif path == "Ac/Consumption/L1/Power" and isinstance(val, (int, float)):
                entry["t1"] = float(val)
                changed = True
            elif path == "Ac/Consumption/L2/Power" and isinstance(val, (int, float)):
                entry["t2"] = float(val)
                changed = True
        elif kind == "battery":
            entry = self._batteries.setdefault(instance, {"instance": instance})
            if path == "Soc" and isinstance(val, (int, float)):
                entry["soc"] = float(val)
                changed = True
            elif path == "Dc/0/Voltage" and isinstance(val, (int, float)):
                entry["voltage"] = float(val)
                changed = True
            elif path == "Dc/0/Current" and isinstance(val, (int, float)):
                entry["current"] = float(val)
                entry["state"] = state_from_current(float(val))
                changed = True
            elif path == "Dc/0/Power" and isinstance(val, (int, float)):
                entry["power"] = float(val)
                changed = True
            elif path == "ProductName" and isinstance(val, str) and val.strip():
                entry.setdefault("name", val.strip())
                changed = True
            elif path == "CustomName" and isinstance(val, str) and val.strip():
                entry["name"] = val.strip()
                changed = True
            elif path == "Serial" and isinstance(val, str) and val.strip():
                entry["serial"] = val.strip()
                changed = True
        elif kind == "solarcharger":
            entry = self._chargers.setdefault(instance, {})
            if path == "Yield/Power" and isinstance(val, (int, float)):
                entry["power"] = float(val)
                changed = True
            elif path == "Pv/V" and isinstance(val, (int, float)):
                entry["pv_voltage"] = float(val)
                changed = True
            elif path == "Dc/0/Current" and isinstance(val, (int, float)):
                entry["current"] = float(val)
                changed = True
            elif path == "ProductName" and isinstance(val, str) and val.strip():
                entry["name"] = val.strip()
                changed = True
            elif path == "Serial" and isinstance(val, str) and val.strip():
                entry["serial"] = val.strip()
                changed = True
        elif kind == "vebus":
            entry = self._vebus.setdefault(instance, {})
            if path in ("Ac/ActiveIn/L1/Power", "Ac/L1/Power") and isinstance(val, (int, float)):
                entry["l1_power"] = float(val)
                changed = True
            elif path in ("Ac/ActiveIn/L2/Power", "Ac/L2/Power") and isinstance(val, (int, float)):
                entry["l2_power"] = float(val)
                changed = True
            elif path in ("Ac/Out/P", "Ac/Power") and isinstance(val, (int, float)):
                entry["ac_power"] = float(val)
                changed = True
            elif path == "Hub4/L1/AcPowerSetpoint" and isinstance(val, (int, float)):
                entry["setpoint"] = float(val)
                changed = True
            elif path == "State" and isinstance(val, (int, float)):
                code = int(val)
                entry["inverter_state"] = INVERTER_STATES.get(code, f"? ({code})")
                changed = True
        if changed:
            self._apply_cerbo_overlays()
        return changed

    def _find_shunt(self) -> dict[str, Any] | None:
        for entry in self._batteries.values():
            name = str(entry.get("name") or "").lower()
            if "shunt" in name:
                return entry
        return None

    def _apply_cerbo_overlays(self) -> None:
        """Write Cerbo device maps into current_state (desktop apply_cerbo_to_state)."""
        if self._acload_powers:
            self._sync_acload_to_state()

        if self._batteries:
            batteries = []
            for inst in sorted(self._batteries, key=lambda x: int(x) if x.isdigit() else 0):
                b = dict(self._batteries[inst])
                b.setdefault("instance", inst)
                batteries.append(b)
            shunt = self._find_shunt()
            if shunt is not None:
                voltage = shunt.get("voltage")
                if isinstance(voltage, (int, float)):
                    self.current_state["battery_soc"] = voltage_soc(float(voltage))
                    self.current_state["battery_voltage"] = float(voltage)
                self.current_state["battery_current"] = float(shunt.get("current") or 0.0)
                self.current_state["battery_power"] = float(shunt.get("power") or 0.0)
            self.current_state["batteries"] = batteries

        if self._chargers:
            chargers = []
            for inst in sorted(self._chargers, key=lambda x: int(x) if x.isdigit() else 0):
                chargers.append(dict(self._chargers[inst]))
            mppt_total = sum(float(c.get("power") or 0.0) for c in chargers)
            self.current_state["mppt_chargers"] = chargers
            self.current_state["mppt_total"] = mppt_total
        else:
            mppt_total = float(self.current_state.get("mppt_total") or 0.0)

        if self._pv_inverters:
            ordered = [
                self._pv_inverters[k]
                for k in sorted(self._pv_inverters, key=lambda x: int(x) if x.isdigit() else 0)
            ]
            self.current_state["pv_inverters"] = ordered
            pv_total = sum(float(p.get("power") or 0.0) for p in ordered)
        else:
            pv_total = 0.0
            for p in self.current_state.get("pv_inverters") or []:
                if isinstance(p, dict):
                    pv_total += float(p.get("power") or 0.0)

        if self._chargers or self._pv_inverters:
            if not self._chargers:
                mppt_total = float(self.current_state.get("mppt_total") or 0.0)
            self.current_state["solar_total"] = mppt_total + pv_total

        # systemcalc grid / consumption (preferred)
        if self._system:
            s = next(iter(self._system.values()))
            if "g1" in s:
                self.current_state["g1"] = s["g1"]
            if "g2" in s:
                self.current_state["g2"] = s["g2"]
            if "t1" in s:
                self.current_state["t1"] = s["t1"]
            if "t2" in s:
                self.current_state["t2"] = s["t2"]
            g1, g2 = self.current_state.get("g1"), self.current_state.get("g2")
            if isinstance(g1, (int, float)) and isinstance(g2, (int, float)):
                self.current_state["gt"] = float(g1) + float(g2)
            t1, t2 = self.current_state.get("t1"), self.current_state.get("t2")
            if isinstance(t1, (int, float)) and isinstance(t2, (int, float)):
                self.current_state["tt"] = float(t1) + float(t2)
            elif isinstance(t1, (int, float)):
                self.current_state["tt"] = float(t1)
            elif isinstance(t2, (int, float)):
                self.current_state["tt"] = float(t2)

        if self._vebus:
            v = next(iter(self._vebus.values()))
            if "g1" not in self.current_state and "l1_power" in v:
                self.current_state["g1"] = v["l1_power"]
            if "g2" not in self.current_state and "l2_power" in v:
                self.current_state["g2"] = v["l2_power"]
            if "gt" not in self.current_state:
                g1, g2 = self.current_state.get("g1"), self.current_state.get("g2")
                if isinstance(g1, (int, float)) and isinstance(g2, (int, float)):
                    self.current_state["gt"] = float(g1) + float(g2)
                elif "ac_power" in v:
                    self.current_state["gt"] = v["ac_power"]
            if "setpoint" in v:
                self.current_state["setpoint"] = v["setpoint"]
            if "inverter_state" in v:
                self.current_state["inverter_state"] = v["inverter_state"]
