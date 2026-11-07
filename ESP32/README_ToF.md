# ToF sensor testing (ESP32 / MicroPython)

This folder contains ToF-only code. LCD, webcam, Wi-Fi credentials, and WebSocket are **not included**.

## Wiring

| Sensor pin | ToF 1 | ToF 2 | ToF 3 | ToF 4 |
|---|---|---|---|---|
| VIN | 3V3 | 3V3 | 3V3 | 3V3 |
| GND | GND | GND | GND | GND |
| SDA | GPIO21 | GPIO21 | GPIO21 | GPIO21 |
| SCL | GPIO22 | GPIO22 | GPIO22 | GPIO22 |
| XSHUT | GPIO25 | GPIO26 | GPIO27 | GPIO32 |

Use a module rated for 3.3V VIN and ensure the I2C pull-up voltage is safe for ESP32.

## Setup

- Flash MicroPython on ESP32.
- Upload the working third-party **`VL53L0X.py`** driver to the ESP32 filesystem. It is **not copied into this repository**. Keep the exact uppercase filename; it is a different driver from lowercase `vl53l0x.py`.
- Open the test script in Thonny and run it on the ESP32 (F5).
- Start with `tof_4_distance.py` to verify all four distance readings.
- Run `tof_zone_movement.py` for A↔B (ToF 1/2) and B↔C (ToF 3/4) direction events only.

## Calibrating detection thresholds

The ON/OFF distance thresholds were selected from an initial miniature model test and may need adjustment if sensors or walls move. ON means entering detection; OFF means leaving. Each gate should be tested in both travel directions repeatedly. Direction inference is an experimental demo: near-simultaneous triggers, U-turns, occlusion and closely spaced multiple objects may produce ambiguous results. Counts are intentionally not maintained.

## Project status

Four-sensor distance reading was confirmed in the model. The full four-sensor direction detector still requires repeated field testing; it should not be treated as production-validated.
