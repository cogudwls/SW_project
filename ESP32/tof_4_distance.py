"""ESP32 MicroPython / VL53L0X x4 distance test. No LCD or Wi-Fi."""
from machine import Pin, I2C
import VL53L0X
import time

XSHUT_PINS = (25, 26, 27, 32)
ADDRESSES = (0x30, 0x31, 0x32, 0x29)

xshuts = [Pin(p, Pin.OUT, value=0) for p in XSHUT_PINS]
time.sleep_ms(300)
i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=100000)
print("ALL OFF:", [hex(a) for a in i2c.scan()])
tofs = []

try:
    for idx, (xshut, addr) in enumerate(zip(xshuts, ADDRESSES), 1):
        xshut.value(1)
        time.sleep_ms(300)
        print("ToF{} before init:".format(idx), [hex(a) for a in i2c.scan()])
        if 0x29 not in i2c.scan():
            raise OSError("ToF{} did not appear at 0x29".format(idx))

        tof = VL53L0X.VL53L0X(i2c)
        if addr != 0x29:
            i2c.writeto_mem(0x29, 0x8A, bytes([addr]))
            tof.address = addr
            time.sleep_ms(100)
        tofs.append(tof)
        print("ToF{} ready:".format(idx), [hex(a) for a in i2c.scan()])

    print("ALL ADDRESSES:", [hex(a) for a in i2c.scan()])
    for tof in tofs:
        tof.start()
    print("Four ToF distance test started (Ctrl+C to stop)")

    while True:
        readings = []
        for tof in tofs:
            try:
                d = tof.read() / 10.0
                readings.append(round(d, 1) if 0 < d <= 120 else "INVALID")
            except OSError:
                readings.append("ERROR")
        print("T1:", readings[0], "| T2:", readings[1],
              "| T3:", readings[2], "| T4:", readings[3])
        time.sleep_ms(100)
except KeyboardInterrupt:
    print("Stopped")
finally:
    for tof in tofs:
        try:
            tof.stop()
        except Exception:
            pass
