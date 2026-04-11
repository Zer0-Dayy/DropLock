
import RPi.GPIO as GPIO

class DoorSensor:
    def __init__(self, pin):
        self.pin = pin
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print(f" Door sensor initialized on GPIO{self.pin}")

    def is_closed(self):
        return GPIO.input(self.pin) == GPIO.LOW

    def status(self):
        state = "CLOSED" if self.is_closed() else "OPEN"
        print(f" Door is {state}")
        return state
