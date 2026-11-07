"""ESP32 MicroPython / 4 VL53L0X zone crossing detector, no headcount or LCD.

This is a demonstration detector based on the first-triggered sensor.
It needs on-site threshold validation; simultaneous triggers and U-turns are ambiguous.
"""
from machine import Pin, I2C
import VL53L0X
import time

XSHUT_PINS = (25, 26, 27, 32)
ADDRESSES = (0x30, 0x31, 0x32, 0x29)
# ToF1..ToF4 measured in user's model; tune after relocating sensors.
ON_CM = (6.5, 9.5, 7.0, 7.7)
OFF_CM = (7.3, 10.5, 8.5, 8.1)
TIMEOUT_MS = 4000

xshuts = [Pin(pin, Pin.OUT, value=0) for pin in XSHUT_PINS]
time.sleep_ms(300)
i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=100000)
tofs = []

try:
    for idx, (xshut, addr) in enumerate(zip(xshuts, ADDRESSES), 1):
        xshut.value(1)
        time.sleep_ms(300)
        if 0x29 not in i2c.scan():
            raise OSError("ToF{} not found at 0x29".format(idx))
        tof = VL53L0X.VL53L0X(i2c)
        if addr != 0x29:
            i2c.writeto_mem(0x29, 0x8A, bytes([addr]))
            tof.address = addr
            time.sleep_ms(100)
        tofs.append(tof)
        print("ToF{} READY".format(idx))

    print("I2C:", [hex(a) for a in i2c.scan()])
    for tof in tofs:
        tof.start()

    detected = [False] * 4
    # 0 = waiting, 1 or 2 = first activated sensor in corresponding gate
    first = [0, 0]
    started_at = [0, 0]
    counted = [False, False]  # processed once per crossing; no people counts
    print("Zone movement detector started")

    while True:
        rising = [False] * 4
        now = time.ticks_ms()

        for i, tof in enumerate(tofs):
            try:
                cm = tof.read() / 10.0
            except (OSError, TimeoutError):
                continue
            if not 0 < cm <= 120:
                continue
            before = detected[i]
            if not before and cm <= ON_CM[i]:
                detected[i] = True
            elif before and cm >= OFF_CM[i]:
                detected[i] = False
            rising[i] = detected[i] and not before

        for gate in range(2):
            a, b = 2 * gate, 2 * gate + 1
            if first[gate] == 0:
                # Wait for either edge; simultaneous edges are ambiguous.
                if rising[a] != rising[b]:
                    first[gate] = 1 if rising[a] else 2
                    started_at[gate] = now
                    counted[gate] = False
                    print("G{} first: ToF{}".format(gate + 1,
                                                  a + 1 if rising[a] else b + 1))
            elif not counted[gate]:
                if first[gate] == 1 and rising[b]:
                    print("MOVEMENT: A -> B" if gate == 0 else "MOVEMENT: B -> C")
                    counted[gate] = True
                elif first[gate] == 2 and rising[a]:
                    print("MOVEMENT: B -> A" if gate == 0 else "MOVEMENT: C -> B")
                    counted[gate] = True

            if first[gate] != 0:
                if counted[gate] and not detected[a] and not detected[b]:
                    first[gate] = 0
                    counted[gate] = False
                    print("G{} READY".format(gate + 1))
                elif time.ticks_diff(now, started_at[gate]) > TIMEOUT_MS:
                    first[gate] = 0
                    counted[gate] = False
                    print("G{} TIMEOUT".format(gate + 1))

        time.sleep_ms(50)
except KeyboardInterrupt:
    print("Stopped")
finally:
    for tof in tofs:
        try:
            tof.stop()
        except Exception:
            pass
