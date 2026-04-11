import RPi.GPIO as GPIO
from Locker_Module import Locker
import time

GPIO.setmode(GPIO.BCM)

# Quick MC38 test
GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)
print(f"Direct GPIO23 test: {GPIO.input(23)}")

lockers = {0: Locker(locker_id=0, relay_pin=17, door_pin=27, mc38_pin=23)}

try:
    while True:
        cmd = input("Command: ").strip().lower().split()
        if not cmd: continue
        action = cmd[0]
        if action == "q": break
        lid = int(cmd[1])
        if action == "u": lockers[lid].unlock()
        if action == "s": lockers[lid].status()

except KeyboardInterrupt:
    print("\nExiting...")

finally:
    GPIO.cleanup()
