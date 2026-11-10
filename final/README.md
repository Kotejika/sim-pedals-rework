# Sim Pedals — complete source

Complete source tree for the nRF52840 pedal node, USB hub and desktop configuration app.

The pedal node has three playable axes: clutch and throttle from SS49E Hall sensors, brake from an ADS1220 differential load cell (AIN0-AIN1). The hub exposes USB HID and configuration telemetry and communicates with the pedal node over Nordic ESB at 2 Mbps.

The Windows app supports calibration, smoothing, deadzone, inversion, response curves, brake load-cell calibration, profiles, hub power settings and ADS1220 diagnostics. Its wallpaper uses an original, fully clothed Sachi-inspired fan illustration; it is not official manga artwork.

ADS1220 diagnostics are transported as a metadata extension, not as extra gamepad axes. Host tests and CI do not prove physical hardware behavior; complete the real-board smoke test before relying on the pedals in a race.
