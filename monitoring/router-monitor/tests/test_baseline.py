from router_monitor.baseline import build_profile, is_anomalous


def test_profile_keeps_vsz_in_model() -> None:
    profile = build_profile([100, 101, 99, 100, 102])
    assert profile is not None
    assert profile.samples == 5
    assert profile.median == 100
    assert profile.p95 > profile.median


def test_anomaly_uses_mad_and_minimum_delta() -> None:
    profile = build_profile([10, 10, 11, 10, 10])
    assert profile is not None
    assert not is_anomalous(11, profile, minimum_delta=2)
    assert is_anomalous(20, profile, minimum_delta=2)
