# Live H3X Charge Validation

Date: 2026-10-26. **Useful physical grid charging remains unresolved.** This
report distinguishes accepted commands from actual battery energy transfer.
Raw home telemetry, network addresses and credentials are not published.

## Verified Configuration

| Item | Read-back / setting |
| --- | --- |
| PCS model register | `FH3X15KHY3PEU` |
| PCS software register, four bytes | `1.0.0.29` |
| Pack | 6 modules, 30.72 kWh nominal, 29.17 kWh usable |
| SOC during tests | 54% |
| BMS temperature | 28-29 C |
| Device grid-import limit, 40937-40938 | 13,800 W |
| BMS maximum charging voltage | 681.6 V |
| BMS maximum charging current | 40 A |
| PCS/BMS charging-forbidden flags | Clear |
| PCS state / grid connection | Normal / connected |

The positive Force Charge Rate is a local requested magnitude. Register 40901 is
a signed effective reference. With configured Pn 15,000 W and a 13,800 W power
cap, requesting 100% correctly produces -92%, raw -920 (`0xFC68`). A 10% request
without limiting produces -10%, raw -100 (`0xFF9C`). The sign/scale therefore
must not be changed merely to make the two displayed values equal.

## Controlled Comparisons

| Control path | Requested rates | Observed battery power |
| --- | --- | --- |
| Installed HA bridge, User reference | 5%, 10%, 20% | 0 to -63 W |
| Installed bridge, PN-Customer slot | 5%, 10%, 20% | 0 W |
| Installed bridge, User slot | 5%; partial 10% | 0 W |
| Local HA runtime, RTC-aligned slot, modes 4 and 5 | 10% each | 0 W |
| RTC-aligned slot plus matching negative reference, modes 4 and 5 | 10% each | 0 to -63 W |
| Reference before User-mode activation, FC06 | 5%, 10%, 20% | 0 to -63 W |
| Reference before User-mode activation, FC16 | 10% | -63 W |

The installed-bridge matrix stopped during the 10% User-slot stage when a Modbus
poll timed out and physical telemetry became unavailable. Recovery to zero
reference, Force Charge off and Self-Consumption was verified. The remaining
20% User-slot stage was not run in that matrix. It must not be counted as a pass.

RTC-aligned tests read back all six slot registers and EMS mode successfully.
The device RTC was approximately one hour behind HA local time. This difference
alone does not establish whether the firmware schedule engine uses RTC time or
applies its own timezone. Both local-time and RTC-aligned comparisons failed to
produce the requested power; the existing local-time basis remains the release
default, with RTC alignment an explicit commissioning option.

In the FC16 comparison, the PCS reported Charging but measured battery current
remained 0.0 A and the BMS switching register was `0x0073`, basic status `3`
(Idle). The measured trickle is not evidence of delivery of a 1.5 kW request.
SOC remained 54%. A Charging UI indication or matching register value
must not be treated as successful useful charging.

## Safety And Restoration

The installed bridge was verified unloaded before each isolated connection.
Only one Modbus client was used at a time. The isolated runtime ran the actual
HA coordinator code with fresh independent Shelly grid measurements, conservative
17.5 kW connection / 11 kW battery caps and 0.5C protection. No maximum-power or
sign-inversion trial was made.

Combined-reference and activation-order tests temporarily lowered the device's
charge-SOC ceiling from 100% to 59%. Each verified a zero-reference stop before
restoring 100%, original schedules and the installed bridge. Force Charge and
Predictive Dispatch control were left off; the earlier optimizer entry remained
disabled. BMS protection, grid-code settings and
firmware were not changed. No whole-server HA restart was performed.

## Software Findings

- Add actual register range and slave to polling failure messages.
- Verify slot contents and mode, expose clock/read-back diagnostics, and disable
  a slot after failed verification. Explicit clock writes default to off.
- Keep local-time slot behavior by default; offer RTC alignment for controlled
  commissioning. Use elapsed-time arithmetic for short slot renewal across DST.
- The predictive dynamic program ran synchronously on HA's event loop. It now
  runs as pure math in HA's executor; HA state access and device writes stay on
  the event loop. This removes a concrete blocking defect that could delay
  Modbus callbacks. It does not prove every timeout had that cause.
- Reject results if settings change while solving. Recheck current SOC,
  temperature, dedicated EV state, live PV/load, grid limits and slot expiry.
- Add tests for responsive HA state updates during a blocked worker, changed
  options, actual optimizer execution, midnight recovery and commissioning
  cleanup after meter failure. Emulator success is not hardware proof.

## Remaining Discriminator

Run an equivalent timed grid-charge request directly in PylontechPro. If that
also fails, commissioning or device/firmware behavior requires installer or
manufacturer investigation. If it works, request the exact documented Modbus
activation sequence for the register-reported software version and compare the
app's device settings against this evidence. Do not guess undocumented enables.

The inverter's grid-power register read zero while the independent Shelly meter
reported import, and active-power-control register 40400 read zero (documented
as No Used). These are reasons to review device-side meter/CT commissioning,
not proof of a particular fault and not authority to change grid protections.
The manufacturer's [PylontechPro SOP](https://pimassetsprdst.blob.core.windows.net/assets/apc_Original/84/54/130168454.pdf)
describes meter/CT, three-phase meter selection and the separate Power from Grid
setting. The upstream maintainer [reports successful User-mode manual control](https://github.com/wietse108/HA-pylontech-force-H3X/issues/18),
so the observations do not establish a universal H3X limitation.

A separate two-hour read-only HA soak records availability, power/SOC and errors
after active tests. It sends no battery commands and cannot certify charging
performance under load.
