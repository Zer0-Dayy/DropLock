import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


def test_locker_manager_open_all_unlocks_each_locker():
    _install_gpio_hx_mocks()
    from Locker_Module import LockerConfig, LockerManager, LockerState

    manager = LockerManager.from_configs(
        [
            LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23),
            LockerConfig(locker_id="Locker 2", relay_pin=18, door_pin=22, mc38_pin=24),
        ]
    )

    status = manager.open_all(duration=0)

    assert status == {"Locker 1": True, "Locker 2": True}
    assert all(locker.state == LockerState.OPEN for locker in manager.lockers.values())
    assert all(locker.expected_closed is False for locker in manager.lockers.values())


def _install_paho_mock():
    for mod in ["main", "paho", "paho.mqtt", "paho.mqtt.client"]:
        sys.modules.pop(mod, None)

    paho = types.ModuleType("paho")
    mqtt_pkg = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class CallbackAPIVersion:
        VERSION2 = 2

    class Client:
        def __init__(self, callback_api_version=None, client_id=None):
            self.callback_api_version = callback_api_version
            self.client_id = client_id
            self.published = []
            self.subscriptions = []
            self.on_connect = None
            self.on_disconnect = None
            self.on_message = None

        def tls_set(self, *args, **kwargs):
            return None

        def tls_insecure_set(self, *args, **kwargs):
            return None

        def reconnect_delay_set(self, *args, **kwargs):
            return None

        def subscribe(self, topic, qos=0):
            self.subscriptions.append((topic, qos))

        def publish(self, topic, payload=None, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))

        def connect(self, *args, **kwargs):
            return None

        def loop_start(self):
            return None

        def loop_stop(self):
            return None

        def disconnect(self):
            return None

    client_mod.CallbackAPIVersion = CallbackAPIVersion
    client_mod.Client = Client
    mqtt_pkg.client = client_mod
    paho.mqtt = mqtt_pkg
    sys.modules["paho"] = paho
    sys.modules["paho.mqtt"] = mqtt_pkg
    sys.modules["paho.mqtt.client"] = client_mod


def test_admin_open_command_opens_target_locker():
    _install_gpio_hx_mocks()
    _install_paho_mock()
    from Locker_Module import LockerConfig, LockerManager, LockerState
    from main import DropLockController, MqttConfig

    manager = LockerManager.from_configs(
        [
            LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23),
            LockerConfig(locker_id="Locker 2", relay_pin=18, door_pin=22, mc38_pin=24),
        ]
    )
    controller = DropLockController(MqttConfig(tls_enabled=False), manager)
    controller._start_weight_stream = lambda locker_id, request_id: None

    controller.handle_admin_open(
        {"type": "OPEN", "requestId": "admin-1", "lockerId": "Locker 2", "unlockDurationSeconds": 0}
    )

    assert manager.get_locker("Locker 1").state == LockerState.CLOSED
    assert manager.get_locker("Locker 2").state == LockerState.OPEN
    assert any('"type":"OPEN_ACK"' in payload for _, payload, _, _ in controller.client.published)
