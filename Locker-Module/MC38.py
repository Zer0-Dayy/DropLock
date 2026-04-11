import RPi.GPIO as GPIO

class MC38:
    def __init__(self, pin):
        self.pin = pin
        print(f"MC38 using BCM pin: {pin}")
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print(f" MC38 initialized on GPIO{self.pin}")

    def is_closed(self):
        val = GPIO.input(self.pin)
        print(f"MC38 raw value: {val}")
        return val == GPIO.LOW

    def status(self):
        state = "CLOSED" if self.is_closed() else "OPEN"
        print(f"🧲 MC38 : {state}")
        return state
