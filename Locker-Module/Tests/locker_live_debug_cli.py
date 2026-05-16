from __future__ import annotations

import logging

import RPi.GPIO as GPIO

from Locker_Module import LockerManager
from main import _run_manual_weight_calibration, build_default_locker_configs


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("droplock.locker_live_debug")


HELP_TEXT = """
Commands:
  uN  -> unlock locker N (example: u1)
  sN  -> show state for locker N (example: s1)
  h   -> show this help
  q   -> quit
""".strip()


def _parse_locker_command(raw: str):
    raw = raw.strip().lower()
    if len(raw) < 2:
        return None, None

    cmd = raw[0]
    index = raw[1:]
    if cmd not in {"u", "s"}:
        return None, None
    if not index.isdigit():
        return None, None

    return cmd, int(index)


def _locker_id_from_index(locker_manager: LockerManager, index: int):
    locker_ids = list(locker_manager.lockers.keys())
    if index < 1 or index > len(locker_ids):
        return None
    return locker_ids[index - 1]


def _print_locker_state(locker_id: str, locker) -> None:
    sensors = locker.sensor_snapshot()
    weight_grams = locker.get_weight_grams()

    print(f"\n[{locker_id}] state")
    print(f"  Locker state machine: {locker.state.value}")
    print(f"  Weight (g): {weight_grams}")
    print(f"  Door closed (combined): {sensors['doorClosed']}")
    print(f"  MC38 closed: {sensors['mc38Closed']}")
    print(f"  Sary XG07E feedback closed: {sensors['feedbackClosed']}")
    print(f"  Lock status: {locker.lock.status()}")


def main() -> int:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    locker_manager = LockerManager.from_configs(build_default_locker_configs())
    startup_open_status = locker_manager.open_all()
    logger.info("Startup locker open status: %s", startup_open_status)

    try:
        calibration_status = _run_manual_weight_calibration(locker_manager)
        logger.info("Manual weight sensor calibration status: %s", calibration_status)
        if not all(calibration_status.values()):
            logger.error("Manual calibration failed for at least one locker; aborting debug script.")
            return 1

        print("\nLocker debug shell is ready.")
        print(HELP_TEXT)

        while True:
            raw = input("\ndebug> ").strip()
            if not raw:
                continue

            lowered = raw.lower()
            if lowered in {"q", "quit", "exit"}:
                print("Exiting locker debug shell.")
                return 0
            if lowered in {"h", "help"}:
                print(HELP_TEXT)
                continue

            cmd, index = _parse_locker_command(raw)
            if cmd is None:
                print("Unknown command. Use h for help.")
                continue

            locker_id = _locker_id_from_index(locker_manager, index)
            if locker_id is None:
                print(f"Locker {index} does not exist.")
                continue

            locker = locker_manager.get_locker(locker_id)
            if locker is None:
                print(f"Locker '{locker_id}' is unavailable.")
                continue

            if cmd == "u":
                print(f"Unlocking {locker_id}...")
                locker.unlock()
                print(f"{locker_id} unlock pulse complete.")
            elif cmd == "s":
                _print_locker_state(locker_id, locker)

    except KeyboardInterrupt:
        print("\nStopping locker debug script.")
        return 0
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
