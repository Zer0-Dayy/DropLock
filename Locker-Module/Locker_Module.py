from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from MC38 import MC38
from Sary_XG07E import Lock
from Sary_XG07E_Feedback import DoorSensor
from Weight_Sensor import WeightSensor


logger = logging.getLogger(__name__)


class LockerState(str, Enum):
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    AWAITING_CLOSE = "AWAITING_CLOSE"
    CLOSING_VERIFY = "CLOSING_VERIFY"
    TAMPER = "TAMPER"


@dataclass
class LockerConfig:
    locker_id: str
    relay_pin: int
    door_pin: int
    mc38_pin: int
    weight_data_pin: int = 6
    weight_clock_pin: int = 5
    weight_threshold: int = 100
    weight_reference_unit: float = 5833.65


class Locker:
    def __init__(self, config: LockerConfig):
        self.config = config
        self.locker_id = config.locker_id

        self.lock = Lock(config.relay_pin)
        self.door_sensor = DoorSensor(config.door_pin)
        time.sleep(0.1)
        self.mc38 = MC38(config.mc38_pin)
        self.weight_sensor = WeightSensor(
            data_pin=config.weight_data_pin,
            clock_pin=config.weight_clock_pin,
            threshold=config.weight_threshold,
            reference_unit=config.weight_reference_unit,
        )

        self.state: LockerState = LockerState.CLOSED
        self.expected_closed = True
        logger.info("Locker %s initialized", self.locker_id)

    def calibrate_weight_sensor(self) -> bool:
        logger.info("Calibrating weight sensor for locker %s", self.locker_id)
        return self.weight_sensor.calibrate()

    def unlock(self, duration=2):
        self.state = LockerState.OPENING
        self.expected_closed = False
        self.lock.unlock(duration)
        self.state = LockerState.OPEN

    def get_weight_grams(self) -> int:
        return self.weight_sensor.get_weight()

    def are_sensors_closed(self) -> bool:
        mc38_closed = self.mc38.is_closed()
        feedback_closed = self.door_sensor.is_closed()
        return mc38_closed and feedback_closed

    def sensor_snapshot(self) -> Dict[str, bool]:
        mc38_closed = self.mc38.is_closed()
        feedback_closed = self.door_sensor.is_closed()
        return {
            "mc38Closed": mc38_closed,
            "feedbackClosed": feedback_closed,
            "doorClosed": mc38_closed and feedback_closed,
        }

    def begin_close_wait(self):
        self.state = LockerState.AWAITING_CLOSE

    def verify_closed(self, close_buffer_seconds=5) -> bool:
        self.state = LockerState.CLOSING_VERIFY
        time.sleep(close_buffer_seconds)
        closed = self.are_sensors_closed()

        if closed:
            self.expected_closed = True
            self.state = LockerState.CLOSED
            self.lock.do_lock()
        else:
            self.state = LockerState.OPEN
        return closed

    def evaluate_tamper(self) -> bool:
        if not self.expected_closed:
            return False

        sensors = self.sensor_snapshot()
        tamper = not sensors["doorClosed"]
        if tamper:
            self.state = LockerState.TAMPER
        elif self.state == LockerState.TAMPER:
            self.state = LockerState.CLOSED
        return tamper


class LockerManager:
    def __init__(self, lockers: Dict[str, Locker]):
        if len(lockers) > 4:
            raise ValueError("Maximum supported lockers is 4")
        self.lockers = lockers

    @classmethod
    def from_configs(cls, configs):
        locker_map = {cfg.locker_id: Locker(cfg) for cfg in configs}
        return cls(locker_map)

    def get_locker(self, locker_id: str) -> Optional[Locker]:
        return self.lockers.get(locker_id)

    def calibrate_weight_sensors(self):
        calibration_status = {}
        for locker_id, locker in self.lockers.items():
            calibration_status[locker_id] = locker.calibrate_weight_sensor()
        return calibration_status

    def heartbeat_snapshot(self):
        status = {}
        for locker_id, locker in self.lockers.items():
            sensors = locker.sensor_snapshot()
            status[locker_id] = {
                "state": locker.state.value,
                "doorClosed": sensors["doorClosed"],
                "tamper": locker.evaluate_tamper(),
            }
        return status
