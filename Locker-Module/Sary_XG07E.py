
import RPi.GPIO as GPIO
import time

class Lock:
    def __init__(self, relay_pin):
        self.relay_pin = relay_pin
        self.is_locked = True
        GPIO.setup(self.relay_pin, GPIO.OUT)
        GPIO.output(self.relay_pin, GPIO.HIGH)
        print(f" Lock initialized on GPIO{self.relay_pin}")

    def unlock(self, duration=2):
        print(f" Unlocking...")
        GPIO.output(self.relay_pin, GPIO.LOW)
        self.is_locked = False
        time.sleep(duration)
        self.do_lock()

    def do_lock(self):
        GPIO.output(self.relay_pin, GPIO.HIGH)
        self.is_locked = True
        print(f" Locked.")

    def status(self):
        return "LOCKED" if self.is_locked else "UNLOCKED"

