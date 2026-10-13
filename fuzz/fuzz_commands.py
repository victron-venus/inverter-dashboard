import json
import sys
import types
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("inverter_dashboard")
package.__path__ = [str(ROOT / "src/inverter_dashboard")]
sys.modules[package.__name__] = package

with atheris.instrument_imports():
    from inverter_dashboard.controller_commands import (
        encode_body,
        validate_override,
        validate_tariff,
    )


def test_one_input(data: bytes) -> None:
    try:
        value = json.loads(data)
    except (ValueError, RecursionError, UnicodeError):
        return
    for validator in (validate_override, validate_tariff):
        try:
            accepted = validator(value)
        except ValueError:
            continue
        assert json.loads(encode_body(accepted)) == accepted  # nosec B101 - intentional test oracle
        assert validator(json.loads(encode_body(accepted))) == accepted  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, atheris.instrument_func(test_one_input))
    atheris.Fuzz()
