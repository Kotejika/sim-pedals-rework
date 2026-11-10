# V5.2 candidate ADS1220 fix

This folder contains a replacement ADS1220 bit-banged SPI driver for the V5.1 source archive.

Changes:
- Samples DOUT after the falling edge, matching the ADS1220 SPI mode 1 timing.
- Checks and propagates GPIO configuration/read/write errors instead of silently returning success.
- Keeps CS asserted for the full RESET, WREG, RREG, START transaction and applies explicit settling delays.

The build workflow downloads the V5.1 archive from branch `from-claude`, overlays `ads1220.c`, and builds both UF2 targets. The resulting firmware is a test candidate; it must be checked on the real controller before being treated as a confirmed fix.
