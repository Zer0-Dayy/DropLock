import sys
import types


def _install_gpio_hx_mocks():
    for mod in ["Locker_Module", "MC38", "Sary_XG07E", "Sary_XG07E_Feedback", "Weight_Sensor"]:
        sys.modules.pop(mod, None)

    gpio = types.ModuleType("GPIO")
    gpio.BCM = 1
    gpio.IN = 1
    gpio.OUT = 0
    gpio.PUD_UP = 1
    gpio.HIGH = 1
    gpio.LOW = 0
    gpio._pins = {}

    def setup(pin, mode, pull_up_down=None):
        gpio._pins.setdefault(pin, gpio.HIGH)

    def input_(pin):
        return gpio._pins.get(pin, gpio.HIGH)

    def output(pin, value):
        gpio._pins[pin] = value

    gpio.setup = setup
    gpio.input = input_
    gpio.output = output
    gpio.setwarnings = lambda *a, **k: None
    gpio.setmode = lambda *a, **k: None
    gpio.cleanup = lambda *a, **k: None

    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio

    hx_mod = types.ModuleType("hx711")

    class HX711:
        def __init__(self, dout_pin, pd_sck_pin):
            self.values = [5833.65 * 1200]

        def reset(self):
            return None

        def get_raw_data(self, times=5):
            return self.values * times

    hx_mod.HX711 = HX711

    sys.modules["RPi"] = rpi
    sys.modules["RPi.GPIO"] = gpio
    sys.modules["hx711"] = hx_mod


def test_locker_open_close_cycle():
    _install_gpio_hx_mocks()
    from Locker_Module import Locker, LockerConfig, LockerState

    locker = Locker(LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23))

    locker.unlock(duration=0)
    assert locker.state == LockerState.OPEN

    locker.begin_close_wait()

    # Simulate door closed on both sensors (LOW)
    import RPi.GPIO as GPIO

    GPIO._pins[23] = GPIO.LOW
    GPIO._pins[27] = GPIO.LOW

    assert locker.verify_closed(close_buffer_seconds=0) is True
    assert locker.state == LockerState.CLOSED


def test_tamper_when_expected_closed_but_sensor_open():
    _install_gpio_hx_mocks()
    from Locker_Module import Locker, LockerConfig, LockerState

    locker = Locker(LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23))

    import RPi.GPIO as GPIO

    # Start closed then force one sensor open
    GPIO._pins[23] = GPIO.HIGH
    GPIO._pins[27] = GPIO.LOW

    assert locker.evaluate_tamper() is True
    assert locker.state == LockerState.TAMPER


def test_manager_calibrates_weight_sensors():
    _install_gpio_hx_mocks()
    from Locker_Module import LockerConfig, LockerManager

    manager = LockerManager.from_configs([LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23)])
    results = manager.calibrate_weight_sensors()

    assert results == {"Locker 1": True}
