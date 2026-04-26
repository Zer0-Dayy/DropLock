import logging
import time

import RPi.GPIO as GPIO


logger = logging.getLogger(__name__)


class Lock:
    """Driver for SARY-XG07E electronic lock via relay.

    Relay semantics in this project:
    * HIGH => locked (relay off)
    * LOW  => unlocked (relay energized)
    """

    def __init__(self, relay_pin):
        self.relay_pin = relay_pin
        self.is_locked = True
        GPIO.setup(self.relay_pin, GPIO.OUT)
        GPIO.output(self.relay_pin, GPIO.HIGH)
        logger.info("Lock initialized on GPIO%s", self.relay_pin)

    def unlock(self, duration=0.5):
        """Pulse unlock for a duration, then return to locked state."""
        logger.info("Unlocking lock on GPIO%s for %ss", self.relay_pin, duration)
        GPIO.output(self.relay_pin, GPIO.LOW)
        self.is_locked = False
        time.sleep(duration)
        self.do_lock()

    def do_lock(self):
        GPIO.output(self.relay_pin, GPIO.HIGH)
        self.is_locked = True
        logger.info("Lock on GPIO%s returned to locked state", self.relay_pin)

    def status(self):
        return "LOCKED" if self.is_locked else "UNLOCKED"
