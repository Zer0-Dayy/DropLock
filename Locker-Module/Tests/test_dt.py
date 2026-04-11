import RPi.GPIO as GPIO
import time

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(6, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(5, GPIO.OUT)

print("Watching DT pin for 5 seconds...")
readings = []
for i in range(50):
    readings.append(GPIO.input(6))
    time.sleep(0.1)

print("Readings:", readings)
print("Highs:", readings.count(1))
print("Lows:", readings.count(0))

GPIO.cleanup()
