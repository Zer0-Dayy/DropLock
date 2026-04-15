import sys
import types


def _install_mocks(raw_sequences):
    sys.modules.pop("Weight_Sensor", None)

    gpio = types.ModuleType("GPIO")
    gpio.setwarnings = lambda *a, **k: None
    gpio.setmode = lambda *a, **k: None
    gpio.cleanup = lambda *a, **k: None

    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio

    hx_mod = types.ModuleType("hx711")

    class HX711:
        def __init__(self, dout_pin, pd_sck_pin):
            self.raw_sequences = [list(seq) for seq in raw_sequences]

        def reset(self):
            return None

        def get_raw_data(self, times=5):
            if self.raw_sequences:
                return self.raw_sequences.pop(0)
            return [0.0] * times

    hx_mod.HX711 = HX711
    sys.modules["RPi"] = rpi
    sys.modules["RPi.GPIO"] = gpio
    sys.modules["hx711"] = hx_mod


def test_manual_calibration_three_objects():
    sequences = [
        [7000.0] * 25,
        [12000.0] * 25,
        [17000.0] * 25,
    ]
    _install_mocks(sequences)

    from Weight_Sensor import WeightSensor

    sensor = WeightSensor(reference_unit=1.0)
    assert sensor.calibrate_manual([1000.0, 2000.0, 3000.0], samples=25, warmup_reads=0)
    assert abs(sensor.reference_unit - 5.0) < 1e-6
    assert abs(sensor.zero_offset - 2000.0) < 1e-6


def test_manual_calibration_rejects_insufficient_points():
    _install_mocks([[0.0, 0.0, 0.0]])
    from Weight_Sensor import WeightSensor

    sensor = WeightSensor(reference_unit=1.0)
    assert not sensor.calibrate_manual([1000.0, 2000.0])
