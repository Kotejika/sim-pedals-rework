# Sim Pedals — audited V2 baseline

Private firmware project for two Pro Micro-form-factor nRF52840 boards: a wireless pedal node and a USB hub.

## Current engineering decision

The V2 source is a **good architectural baseline**. We are not doing a full rewrite right now. We will preserve its modular structure and harden identified weak points incrementally.

The original V2 author states that it compiled and passed host tests but was **not tested on real hardware**. V4 exists only as the uploaded UF2 binaries; its source is not available. V2 is not V4.

See [SOURCE_AUDIT_RU.md](SOURCE_AUDIT_RU.md) for the audit.

## Known hardware

- Brake: Mavin NA151, 200 kg, 4-wire bridge, nominal sensitivity 2 mV/V.
- ADC: purple ADS1220 breakout powered at 3.3 V.
- ADS1220: SCLK D2, MOSI D3, MISO D4, CS D5, DRDY D6; E+ to AVDD, E- to AGND, S+ to AIN0, S- to AIN1; CLK tied to DGND.
- Clutch and throttle: SS49E Hall sensors through nRF SAADC.
- Two Pro Micro-form-factor nRF52840 boards: pedal node and USB hub.

Exact board revision and P0.13 external sensor-rail switch polarity must be verified before flashing. A measured 3.3 V at ADS supply pins does not verify the rail-control circuit.

## Architecture already present in V2

- Nordic Enhanced ShockBurst (ESB), 2 Mbps, PRX at the hub and PTX at the pedal node. This is not BLE.
- ADS1220, differential load-cell acquisition, intended 1000 SPS.
- SS49E Hall sensing via SAADC.
- Shared C axis processing: filtering, calibration, invert, deadzone, response curve.
- USB HID joystick plus vendor HID configuration interface.
- Windows calibration app and host-side C/Python tests.

## Cloud build

The original source archive is stored as eight Base64 text chunks under `source-archive/`; this is only a transport representation. The workflow reconstructs and extracts the actual source tree before building.

Open the [Actions tab](../../actions/workflows/build-firmware.yml) and select **Build Sim Pedals (NCS 3.1.1)**. The workflow builds both Pro Micro UF2 targets in Nordic's NCS 3.1.1 toolchain container and uploads:
- `pedals.uf2`
- `hub.uf2`
- ELF files and build configuration snapshots

After a successful run, download the artifact named `sim-pedals-firmware-<commit>` from that run.

## Priorities

1. Verify sensor rail and pin mapping on the actual boards.
2. Harden ADS1220 initialization, SPI error checking, register readback, DRDY fault handling and recovery.
3. Fix/verify ESB ACK-payload queue/event ordering; add sequence-gap, packet-loss, duplicate, latency and jitter counters.
4. Align Hub live telemetry with the Windows app's expected diagnostics fields.
5. Build both targets reproducibly with NCS v3.1.1, then validate on real hardware.
6. Measure true sensor-to-USB latency, jitter, report cadence, ADC noise and brake repeatability. Do not treat configured 1 kHz as proof of measured 1 kHz end-to-end.

## Safety and release rules

- Keep original V4 UF2 files as the rollback option.
- Do not flash V2-derived changes until both UF2 targets compile and rail, SPI and failsafe behavior are reviewed.
- Successful compilation is not hardware validation.
- Tune for predictable and repeatable brake control, not a marketing frequency number. Firmware can improve pedal consistency but cannot guarantee 5k iRating.
