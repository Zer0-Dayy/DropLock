import logging

import RPi.GPIO as GPIO


logger = logging.getLogger(__name__)


class MC38:
    """Magnetic reed switch sensor for door closed/open detection."""

    def __init__(self, pin):
        self.pin = pin
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info("MC38 initialized on GPIO%s", self.pin)

    def is_closed(self):
        # Pull-up input: LOW means magnet present and door closed.
        return GPIO.input(self.pin) == GPIO.LOW

    def status(self):
        state = "CLOSED" if self.is_closed() else "OPEN"
        logger.info("MC38 state on GPIO%s: %s", self.pin, state)
        return state
