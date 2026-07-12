from unittest.mock import patch

from config_values import env_float, env_int


def test_integer_environment_helpers_fall_back_and_apply_minimums():
    with patch.dict("os.environ", {"BROKEN_INT": "not-a-number"}):
        assert env_int("BROKEN_INT", 3, minimum=1) == 3
    with patch.dict("os.environ", {"BROKEN_INT": "-5"}):
        assert env_int("BROKEN_INT", 3, minimum=1) == 1


def test_numeric_environment_helpers_apply_upper_bounds_and_float_fallback():
    with patch.dict("os.environ", {"VALUE": "70000", "FLOAT": "invalid"}):
        assert env_int("VALUE", 6333, minimum=1, maximum=65535) == 65535
        assert env_float("FLOAT", 20.0, minimum=0.1) == 20.0
