import RPi.GPIO as GPIO
from hx711 import HX711

class WeightSensor:
    def __init__(self, data_pin=6, clock_pin=5, threshold=100):
        self.threshold = threshold
        self.data_pin = data_pin
        self.clock_pin = clock_pin
        self.reference_unit = 5833.65
        GPIO.setwarnings(False)
        self.hx = HX711(dout_pin=6, pd_sck_pin=5)
        self.hx.reset()
        print(f"  Weight sensor initialized on GPIO{data_pin}")

    def get_weight(self):
        try:
            raw = self.hx.get_raw_data(times=5)
            raw_mean = sum(raw) / len(raw)
            weight = max(0, int(raw_mean / self.reference_unit))
            return weight
        except:
            return 0

    def has_object(self):
        return self.get_weight() > self.threshold

    def status(self):
        weight = self.get_weight()
        has_obj = "YES" if weight > self.threshold else "NO"
        print(f"  Weight : {weight}g | Object : {has_obj}")
        return weight
