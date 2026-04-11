import logging
from statistics import mean

import RPi.GPIO as GPIO
from hx711 import HX711


logger = logging.getLogger(__name__)


class WeightSensor:
    def __init__(
        self,
        data_pin=6,
        clock_pin=5,
        threshold=100,
        reference_unit=5833.65,
        read_samples=15,
        trim_count=2,
    ):
        self.threshold = threshold
        self.data_pin = data_pin
        self.clock_pin = clock_pin
        self.reference_unit = reference_unit
        self.read_samples = read_samples
        self.trim_count = trim_count
        self.tare_offset = 0.0

        GPIO.setwarnings(False)
        self.hx = HX711(dout_pin=self.data_pin, pd_sck_pin=self.clock_pin)
        self.hx.reset()
        logger.info("Weight sensor initialized (DT=%s, SCK=%s)", self.data_pin, self.clock_pin)

    def _trimmed_mean(self, values):
        if not values:
            return 0.0
        ordered = sorted(values)
        trim = min(self.trim_count, max(0, (len(ordered) - 1) // 2))
        if trim > 0:
            ordered = ordered[trim:-trim]
        return mean(ordered)

    def calibrate(self, tare_samples=30):
        """Capture empty-scale baseline at startup for better accuracy."""
        try:
            raw = self.hx.get_raw_data(times=tare_samples)
            if not raw:
                logger.warning("Calibration failed: no raw data received")
                return False
            self.tare_offset = self._trimmed_mean(raw)
            logger.info(
                "Weight sensor calibrated with %s samples (tare offset=%.2f)",
                len(raw),
                self.tare_offset,
            )
            return True
        except Exception as exc:
            logger.exception("Weight calibration failed: %s", exc)
            self.tare_offset = 0.0
            return False

    def get_weight(self):
        try:
            raw = self.hx.get_raw_data(times=self.read_samples)
            if not raw:
                return 0

            raw_mean = self._trimmed_mean(raw)
            adjusted = raw_mean - self.tare_offset
            return max(0, int(adjusted / self.reference_unit))
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
