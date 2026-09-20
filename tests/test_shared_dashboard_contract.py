"""Execute the same versioned fixtures as Go, Vue and Tauri."""

import hashlib
import json
from pathlib import Path

import pytest

from inverter_dashboard.websocket_handler import _control_flag_key, control_boolean

ROOT = Path(__file__).resolve().parents[1] / "contracts/dashboard"
FIXTURES = json.loads((ROOT / "v1/fixtures.json").read_text())


def test_fixture_lock():
    lock = json.loads((ROOT / "contract-lock.json").read_text())
    for name, digest in lock["sha256"].items():
        assert hashlib.sha256((ROOT / "v1" / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("case", FIXTURES["key_cases"])
def test_shared_flag_keys(case):
    assert _control_flag_key(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURES["boolean_cases"])
def test_shared_observation_values(case):
    assert control_boolean(case["input"]) is case["expected"]
