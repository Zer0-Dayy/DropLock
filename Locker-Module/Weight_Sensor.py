import logging

import RPi.GPIO as GPIO
from hx711 import HX711


logger = logging.getLogger(__name__)


class WeightSensor:
    def __init__(self, data_pin=6, clock_pin=5, threshold=100, reference_unit=5833.65):
        self.threshold = threshold
        self.data_pin = data_pin
        self.clock_pin = clock_pin
        self.reference_unit = reference_unit

        GPIO.setwarnings(False)
        self.hx = HX711(dout_pin=self.data_pin, pd_sck_pin=self.clock_pin)
        self.hx.reset()
        logger.info("Weight sensor initialized (DT=%s, SCK=%s)", self.data_pin, self.clock_pin)

    def get_weight(self):
        try:
            raw = self.hx.get_raw_data(times=5)
            if not raw:
                return 0
            raw_mean = sum(raw) / len(raw)
            return max(0, int(raw_mean / self.reference_unit))
        except Exception as exc:
            logger.exception("Weight read failed: %s", exc)
            return 0

    def has_object(self):
        return self.get_weight() > self.threshold

    def status(self):
        weight = self.get_weight()
        has_obj = "YES" if weight > self.threshold else "NO"
        logger.info("Weight=%sg | object_present=%s", weight, has_obj)
        return weight
