# Sim Pedals — audited V2 baseline

Private firmware project for two Pro Micro-form-factor nRF52840 boards: a wireless pedal node and a USB hub.

## Current engineering decision

The V2 source is a **good architectural baseline**. We are not doing a full rewrite right now. We will preserve the modular structure and harden the identified weak points incrementally.

V2 source has not been tested on real hardware (the original README says this explicitly). V4 exists only as uploaded UF2 binaries; its source code is not available. Do not claim V2 is V4 or that the V4 source has been recovered.

See [SOURCE_AUDIT_RU.md](SOURCE_AUDIT_RU.md) for the detailed audit and next steps.

## Known hardware

- Brake: Mavin NA151, 200 kg, 4-wire bridge, nominal sensitivity 2 mV/V.
- ADC: purple ADS1220 breakout powered at 3.3 V.
- ADS1220: SCLK D2, MOSI D3, MISO D4, CS D5, DRDY D6; E+ to AVDD, E- to AGND, S+ to AIN0, S- to AIN1; CLK tied to DGND.
- Clutch and throttle: SS49E Hall sensors through nRF SAADC.
- Two Pro Micro-form-factor nRF52840 boards: pedal node and USB hub.

Exact board revision and P0.13 external sensor-rail switch polarity must be verified before flashing. The user's measured 3.3 V at ADS supply pins is a useful check, but does not by itself verify the rail-control circuit.

## Architecture already present in V2

- Nordic Enhanced ShockBurst (ESB), 2 Mbps, PRX at the hub and PTX at the pedal node. This is not BLE.
- ADS1220, differential load-cell acquisition, intended 1000 SPS.
- SS49E Hall sensing via SAADC.
- Shared C axis processing: filtering, calibration, invert, deadzone, response curve.
- USB HID joystick plus vendor HID configuration interface.
- Windows calibration app and host-side C/Python tests.

## Priorities

1. Verify sensor rail and pin mapping on the actual boards.
2. Harden ADS1220 initialization, SPI error checking, register readback, DRDY fault handling and recovery.
3. Fix/verify ESB ACK-payload queue/event ordering; add sequence-gap, packet-loss, duplicate, latency and jitter counters.
4. Align Hub live telemetry with the Windows app's expected diagnostics fields.
5. Build both targets reproducibly with NCS v3.1.1, then validate on the real hardware.
6. Measure true sensor-to-USB latency, jitter, report cadence, ADC noise and brake repeatability. Do not treat a configured 1 kHz as proof of a measured 1 kHz end-to-end rate.

## Safety and release rules

- Keep the original V4 UF2 files as the rollback option.
- Do not flash V2-derived changes until both UF2 targets compile and the rail, SPI and failsafe behavior are reviewed.
- A successful compile is not hardware validation.
- Tune for predictable and repeatable brake control, not a marketing frequency number. Firmware can improve pedal consistency but cannot guarantee 5k iRating.
