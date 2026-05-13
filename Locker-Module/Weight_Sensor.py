import logging
import statistics

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

    def _read_stable_raw(self, samples=25, warmup_reads=5, windows=1, trim_ratio=0.2):
        for _ in range(warmup_reads):
            self.hx.get_raw_data(times=3)
        window_means = []
        for _ in range(windows):
            reading = self._read_filtered_raw(samples=samples, trim_ratio=trim_ratio)
            if reading is not None:
                window_means.append(reading)
        if not window_means:
            return None
        return statistics.median(window_means)

    def calibrate(self, samples=25, warmup_reads=5):
        try:
            reading = self._read_stable_raw(samples=samples, warmup_reads=warmup_reads)
            if reading is None:
                logger.warning("Weight calibration failed: no raw samples")
                return False

            self.zero_offset = reading
            logger.info("Weight sensor calibrated. zero_offset=%.2f", self.zero_offset)
            return True
        except Exception as exc:
            logger.exception("Weight calibration failed: %s", exc)
            return False

    def calibrate_manual(self, known_weights_grams, samples=25, warmup_reads=5, before_read=None):
        """
        Calibrate using known reference objects.
        `known_weights_grams` must contain at least 3 positive values.
        """
        try:
            if len(known_weights_grams) < 3:
                logger.warning("Manual calibration requires at least 3 known objects")
                return False

            if any(weight <= 0 for weight in known_weights_grams):
                logger.warning("Manual calibration weights must be positive")
                return False

            raw_points = []
            for idx, known_weight in enumerate(known_weights_grams, start=1):
                if before_read:
                    before_read(idx, known_weight)
                reading = self._read_stable_raw(samples=samples, warmup_reads=warmup_reads)
                if reading is None:
                    logger.warning("Manual calibration failed for object #%s: no samples", idx)
                    return False
                raw_points.append((float(known_weight), float(reading)))

            # Fit raw = a + b * grams via least squares.
            count = len(raw_points)
            sum_x = sum(weight for weight, _ in raw_points)
            sum_y = sum(raw for _, raw in raw_points)
            sum_xx = sum(weight * weight for weight, _ in raw_points)
            sum_xy = sum(weight * raw for weight, raw in raw_points)

            denominator = (count * sum_xx) - (sum_x * sum_x)
            if denominator == 0:
                logger.warning("Manual calibration failed: degenerate reference weights")
                return False

            slope = ((count * sum_xy) - (sum_x * sum_y)) / denominator
            intercept = (sum_y - (slope * sum_x)) / count

            if slope <= 0:
                logger.warning("Manual calibration failed: non-positive slope %.4f", slope)
                return False

            self.reference_unit = slope
            self.zero_offset = intercept
            logger.info(
                "Manual calibration complete. reference_unit=%.4f zero_offset=%.2f points=%s",
                self.reference_unit,
                self.zero_offset,
                raw_points,
            )
            return True
        except Exception as exc:
            logger.exception("Manual calibration failed: %s", exc)
            return False

    def get_weight(self):
        try:
            raw_median = self._read_stable_raw(
                samples=15,
                warmup_reads=2,
                windows=7,
                trim_ratio=0.25,
            )
            if raw_median is None:
                return 0

            net_raw = raw_median - self.zero_offset
            if abs(net_raw) < self.reference_unit * 0.5:
                return 0
            return max(0, int(round(net_raw / self.reference_unit)))
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
