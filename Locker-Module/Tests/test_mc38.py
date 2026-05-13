import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)

print("Testing MC38...")

while True:
    state = GPIO.input(23)
    if state == 0:
        print("CLOSED")
    else:
        print("OPEN")
    time.sleep(0.5)
