# H3X Bridge 0.4.1

- Fix the eight new override/weather controls receiving device-name-dependent IDs. Assign documented IDs and migrate recognizable automatically generated 0.4.0 IDs; preserve deliberate custom IDs/names and occupied targets.
- Read saved schedules in six-register blocks instead of requiring a 24-register read. Surface failed preparation and zero-headroom pauses as actionable errors rather than apparently successful charging requests.
- Verify register 40901 and EMS mode immediately after direct power writes, with requested signed value, wire word, acknowledgement and read-back diagnostics. Negative still means charge: -10% becomes signed -100, wire word FF9C. Time-slot power remains unsigned with Charge mode 0.
- Separate requested power, verified register reference, grid headroom and measured battery response. Dashboard attributes exist even before the first command; unavailable measurements are null, not invented zeros.
- Test real Home Assistant entity registration, upgrade migration and dashboard attribute contracts alongside signed TCP frames, control ownership, weather reserve and failure paths against the local Modbus emulator.

Requires Home Assistant 2025.3 or newer. Restart Home Assistant after updating. The eight new default dashboard IDs are repaired without reimporting YAML; custom-renamed entities still require matching YAML. Updated controller dashboards add charging-headroom and register-verification rows. Imported storage-mode dashboards need a fresh import for new rows; YAML-mode dashboards referencing packaged files follow package updates.

No physical inverter or live installation was connected. Matching register read-back establishes command acceptance, not physical charging. Test at a low rate and check measured Battery Power/Status and any pause reason. This release does not bypass inverter/BMS interlocks.
