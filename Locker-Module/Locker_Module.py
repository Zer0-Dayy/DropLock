import time
from Sary_XG07E import Lock
from Sary_XG07E_Feedback import DoorSensor
from MC38 import MC38
from Weight_Sensor import WeightSensor

class Locker:
    def __init__(self, locker_id, relay_pin, door_pin, mc38_pin):
        self.locker_id     = locker_id
        self.lock          = Lock(relay_pin)
        self.door_sensor   = DoorSensor(door_pin)
        time.sleep(0.5)
        self.mc38          = MC38(mc38_pin)
        self.weight_sensor = WeightSensor()
        print(f" Locker {self.locker_id} ready!")

    def unlock(self, duration=2):
        self.lock.unlock(duration)

    def status(self):
        print(f"\n── Locker {self.locker_id} Status ──")
        print(f"  Lock : {self.lock.status()}")
        self.door_sensor.status()
        self.mc38.status()
        self.weight_sensor.status()
        print(f"─────────────────────────")

