import logging

import RPi.GPIO as GPIO


logger = logging.getLogger(__name__)


class DoorSensor:
    """Feedback line from lock/door mechanism.

    LOW means physically closed (wired with pull-up).
    """

    def __init__(self, pin):
        self.pin = pin
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info("Door feedback sensor initialized on GPIO%s", self.pin)

    def is_closed(self):
        return GPIO.input(self.pin) == GPIO.LOW

    def status(self):
        state = "CLOSED" if self.is_closed() else "OPEN"
        logger.info("Door feedback state on GPIO%s: %s", self.pin, state)
        return state
