from __future__ import annotations

import logging
import time

import RPi.GPIO as GPIO

from Weight_Sensor import WeightSensor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("droplock.weight_live_debug")


def _read_known_weight(index: int) -> float:
    while True:
        value = input(f"Enter known weight for object #{index} in grams: ").strip()
        try:
            grams = float(value)
        except ValueError:
            print("Invalid number, please try again.")
            continue
        if grams <= 0:
            print("Weight must be greater than 0.")
            continue
        return grams


def _run_manual_calibration(sensor: WeightSensor) -> bool:
    print("\nManual calibration (3 objects)")
    known_weights = [_read_known_weight(i) for i in (1, 2, 3)]

    def _prompt_before_read(index, known_weight):
        input(
            f"\nPlace object #{index} ({known_weight}g) on the scale, wait for it to settle, then press Enter..."
        )

    return sensor.calibrate_manual(
        known_weights,
        samples=25,
        warmup_reads=5,
        before_read=_prompt_before_read,
    )


def main() -> int:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    sensor = WeightSensor(data_pin=6, clock_pin=5, threshold=100, reference_unit=5833.65)

    try:
        for pass_number in (1, 2, 3):
            print(f"\n--- Calibration pass {pass_number}/3 ---")
            if not _run_manual_calibration(sensor):
                logger.error("Calibration pass %s failed.", pass_number)
                return 1

        print("\nCalibration complete. Streaming weight readings. Press Ctrl+C to stop.")
        while True:
            weight = sensor.get_weight()
            logger.info("Live weight reading: %sg", weight)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping weight debug script.")
        return 0
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
