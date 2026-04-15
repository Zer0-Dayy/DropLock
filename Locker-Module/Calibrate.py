import RPi.GPIO as GPIO

from Weight_Sensor import WeightSensor


def _read_known_weight(index):
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


def main():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    sensor = WeightSensor(data_pin=6, clock_pin=5)

    print("\nManual initial calibration (3 objects)")
    print("Use three known objects and place only one object on the scale per step.")
    print("Remove all weight from the scale before starting.\n")

    known_weights = [_read_known_weight(1), _read_known_weight(2), _read_known_weight(3)]

    def _prompt_before_read(index, known_weight):
        input(
            f"\nPlace object #{index} ({known_weight}g) on the scale, wait for it to settle, then press Enter..."
        )

    ok = sensor.calibrate_manual(known_weights, samples=25, warmup_reads=5, before_read=_prompt_before_read)
    if not ok:
        print("\nCalibration failed. Please retry with stable objects and known weights.")
    else:
        print("\nCalibration complete.")
        print(f"zero_offset = {sensor.zero_offset:.2f}")
        print(f"reference_unit = {sensor.reference_unit:.4f} raw-units/gram")
        print("\nUse these values in your locker config for consistent readings.")

    GPIO.cleanup()


if __name__ == "__main__":
    main()
