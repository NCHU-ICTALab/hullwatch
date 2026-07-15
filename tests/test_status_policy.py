from app.api.service import classify_operational_status


def test_operational_status_uses_fixed_speed_loss_thresholds_and_forecast_override():
    assert classify_operational_status(4.99, None) == "ok"
    assert classify_operational_status(5.0, None) == "watch"
    assert classify_operational_status(9.99, None) == "watch"
    assert classify_operational_status(10.0, None) == "action"

    assert classify_operational_status(4.0, 60) == "watch"
    assert classify_operational_status(4.0, 61) == "ok"
    assert classify_operational_status(4.0, 0) == "action"
