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
        self.zero_offset = 0.0

        GPIO.setwarnings(False)
        self.hx = HX711(dout_pin=self.data_pin, pd_sck_pin=self.clock_pin)
        self.hx.reset()
        logger.info("Weight sensor initialized (DT=%s, SCK=%s)", self.data_pin, self.clock_pin)

    def _read_filtered_raw(self, samples=12, trim_ratio=0.2):
        raw = self.hx.get_raw_data(times=samples)
        if not raw:
            return None

        ordered = sorted(raw)
        trim_count = int(len(ordered) * trim_ratio)

        if trim_count * 2 >= len(ordered):
            trimmed = ordered
        else:
            trimmed = ordered[trim_count : len(ordered) - trim_count]

        if not trimmed:
            return None
        return sum(trimmed) / len(trimmed)

    def calibrate(self, samples=25, warmup_reads=5):
        try:
            for _ in range(warmup_reads):
                self.hx.get_raw_data(times=3)
            reading = self._read_filtered_raw(samples=samples)
            if reading is None:
                logger.warning("Weight calibration failed: no raw samples")
                return False

            self.zero_offset = reading
            logger.info("Weight sensor calibrated. zero_offset=%.2f", self.zero_offset)
            return True
        except Exception as exc:
            logger.exception("Weight calibration failed: %s", exc)
            return False

    def get_weight(self):
        try:
            raw_mean = self._read_filtered_raw(samples=12)
            if raw_mean is None:
                return 0

            net_raw = raw_mean - self.zero_offset
            return max(0, int(net_raw / self.reference_unit))
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
