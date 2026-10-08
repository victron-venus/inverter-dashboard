"""Authority and partial-state boundaries for incoming native messages."""

import copy

import pytest

from inverter_dashboard import cerbo, config
from inverter_dashboard.server import MqttState


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(config, "CERBO_PORTAL_ID", "site")
    monkeypatch.setattr(config, "WATER_PUMP_INSTANCE", 7)
    monkeypatch.setattr(cerbo.time, "time", lambda: 1000.0)
    monkeypatch.setattr(cerbo.time, "monotonic", lambda: 100.0)
    result = MqttState()
    result._cerbo_devices = {"pump": {"7": {"Mode": 1, "State": 1}}}
    result._water_mode_observations = {"7": 99.0}
    return result


@pytest.mark.parametrize("payload", [b"garbage", b"{}", b'{"value":true}'])
def test_invalid_mode_revokes_previous_authority_before_rejecting(state, payload):
    before = copy.deepcopy(state.current_state)
    assert state._handle_cerbo_device("N/site/pump/7/Mode", payload) is False
    assert state._water_mode_observations == {}
    assert state._cerbo_devices["pump"]["7"] == {"Mode": 1, "State": 1}
    assert state.current_state == before
    assert state._native_observations == {}


@pytest.mark.parametrize("payload", [b"", b'{"value":null}'])
def test_empty_mode_removes_service_while_null_keeps_other_leaves(state, payload):
    assert state._handle_cerbo_device("N/site/pump/7/Mode", payload) is True
    if payload:
        assert state._cerbo_devices["pump"]["7"] == {"Mode": None, "State": 1}
    else:
        assert state._cerbo_devices["pump"] == {}
    assert state._water_mode_observations == {}
    assert state._native_observations["mqtt"] == (1000.0, 100.0)


@pytest.mark.parametrize("retained", [False, True])
def test_only_nonretained_valid_mode_renews_command_authority(state, retained):
    assert (
        state._handle_cerbo_device("N/site/pump/7/Mode", b'{"value":2}', retained=retained) is True
    )
    assert state._cerbo_devices["pump"]["7"]["Mode"] == 2
    assert state._water_mode_observations == ({} if retained else {"7": 100.0})


@pytest.mark.parametrize(
    "failure_index",
    range(4),
    ids=["observe_mode", "claim_null", "note_receipt", "apply_overlay"],
)
def test_hook_failure_keeps_prior_mutations_and_stops_later_work(state, monkeypatch, failure_index):
    hooks = [
        "_observe_water_mode",
        "_claim_invalid_leaf",
        "_note_native_observation",
        "_apply_cerbo_overlays",
    ]
    calls = []
    error = RuntimeError("injected observer failure")
    before = copy.deepcopy(state.current_state)

    def intercept(name, original):
        def observed(*args):
            calls.append(name)
            assert state._cerbo_devices["pump"]["7"]["Mode"] is None
            assert state._water_mode_observations == {}
            if name == hooks[failure_index]:
                raise error
            return original(*args)

        return observed

    for name in hooks:
        monkeypatch.setattr(state, name, intercept(name, getattr(state, name)))
    with pytest.raises(RuntimeError) as caught:
        state._handle_cerbo_device("N/site/pump/7/Mode", b'{"value":null}')
    assert caught.value is error
    assert calls == hooks[: failure_index + 1]
    assert state.current_state == before
    assert state._native_last_emit is None
    assert bool(state._cerbo_claimed_keys) is (failure_index >= 2)
    assert state._native_observations == ({"mqtt": (1000.0, 100.0)} if failure_index == 3 else {})
