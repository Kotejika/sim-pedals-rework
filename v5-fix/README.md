# V5.2 candidate ADS1220 fix

This folder contains a replacement ADS1220 bit-banged SPI driver for the V5.1 source archive.

Changes:
- Follows ADS1220 SPI mode 1: DIN is sampled by the ADC on SCLK falling edges; DOUT is shifted by the ADC on rising edges and sampled by the MCU after the falling edge.
- Checks and propagates GPIO configuration/read/write errors instead of silently treating all transfers as successful.
- Keeps CS asserted for complete commands and adds conservative setup/hold delays.

The build workflow uses Python zipfile (not the absent unzip utility) to unpack V5.1, overlays this driver, and builds both UF2 targets.

This is a test candidate, not yet a confirmed hardware fix. It must pass a clean firmware build and then be verified on the actual pedal node by reading ADS1220 registers and observing conversions.
