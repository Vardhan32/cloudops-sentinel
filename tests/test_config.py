"""Configuration validation tests."""

import pytest

from app import config


def test_default_config_valid():
    config.validate_config()  # must not raise


@pytest.mark.parametrize(
    "overrides",
    [
        {"CPU_WARN": 90.0, "CPU_CRIT": 90.0},  # warn == crit
        {"CPU_WARN": 95.0, "CPU_CRIT": 90.0},  # warn > crit
        {"MEM_WARN": -1.0, "MEM_CRIT": 90.0},  # out of range
        {"DISK_WARN": 80.0, "DISK_CRIT": 101.0},  # out of range
    ],
)
def test_invalid_thresholds_rejected(monkeypatch, overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(config, key, value)
    with pytest.raises(ValueError):
        config.validate_config()


def test_invalid_intervals_rejected(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_INTERVAL_SECONDS", 1)
    with pytest.raises(ValueError):
        config.validate_config()
