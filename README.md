# Sim Pedals — purpose-built firmware

This repository is the clean implementation of a DIY sim-racing pedal system for two nRF52840 Pro Micro boards: a wireless pedal node and a USB hub.

## Primary objective

Deliver a predictable, repeatable brake signal with low measured latency, reliable wireless transport, stable USB HID reporting, and transparent diagnostics. The target is consistent brake control for iRacing. Firmware cannot guarantee a particular iRating; driving skill, setup, technique, and practice remain decisive.

## Known hardware context

- Brake: Mavin NA151, 200 kg, 4-wire bridge, nominal sensitivity 2 mV/V.
- ADC: purple ADS1220 breakout, supplied at 3.3 V.
- ADS1220 wiring described by the user: SCLK D2, MOSI D3, MISO D4, CS D5, DRDY D6; bridge E+ to AVDD, E- to AGND, S+ to AIN0, S- to AIN1; CLK tied to DGND.
- Controllers: Pro Micro-form-factor nRF52840 boards, one at pedals and one at USB hub.
- Clutch/throttle sensor type and exact board revision still need explicit confirmation.

Treat pin maps and external-power-switch polarity as hardware facts to verify, not assumptions copied blindly from a prior revision.

## Engineering rules

1. Treat the V4 UF2 files as black-box reference binaries only; do not claim to have V4 sources.
2. Treat the V2 archive as a reference for protocol and hardware history, not as an authoritative implementation.
3. Build a clean, testable implementation. Keep hardware abstraction, sensor drivers, radio protocol, HID reporting, storage, and PC configuration app separate.
4. No firmware is called hardware-validated until tested on the actual two boards and ADS1220/load cell.
5. Measure sample rate, report rate, end-to-end latency, jitter, ADC noise, repeatability, radio packet loss, and recovery from disconnects.
6. Never flash a release over the known-working V4 without a verified rollback path.

## Planned deliverables

- Pedal-node firmware UF2.
- Hub firmware UF2 with USB HID joystick endpoint and configuration interface.
- Windows configuration/calibration application.
- Automated host tests, reproducible cloud builds, build logs, and release artifacts.
- Hardware bring-up checklist and measured performance report.
