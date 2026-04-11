import RPi.GPIO as GPIO
from hx711 import HX711

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

hx = HX711(dout_pin=6, pd_sck_pin=5)
hx.reset()

print("Getting empty reading...")
empty = hx.get_raw_data(times=5)
empty_mean = sum(empty) / len(empty)
print("Empty mean:", empty_mean)

input("Place your known weight and press Enter...")
raw = hx.get_raw_data(times=5)
raw_mean = sum(raw) / len(raw)
print("Weight mean:", raw_mean)

diff = raw_mean - empty_mean
known_grams = float(input("How many grams was that? "))
factor = diff / known_grams
print(f"\nYour reference unit is: {factor:.2f}")

GPIO.cleanup()
