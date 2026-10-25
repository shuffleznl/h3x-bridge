# H3X Bridge 0.4.2

- Replace the ambiguous "Command failed or control is owned" message with separate ownership-rejection and actual Modbus write/read-back errors. Record the competing entity, register, value and owner.
- Preserve the direct force-charge owner path and authenticated negative-reference changes. Do not bypass ownership locks to hide a competing command.
- Separate stable control intent from measured charging response. Confirmation requires two successive Charging-status observations with battery power at or below -50 W; loss of either clears confirmation. Requested wattage is not assumed to be delivered.
- Expose charging response, the last rejected entity and the actual power-command failure, including when a failed force-charge service leaves entities unavailable. Identify pending restoration as its own ownership state.
- Keep local override controls and diagnostic entities available through coordinator failures, so errors remain visible and stopping Force Charge is still possible. Physical telemetry remains unavailable until a successful poll.
- Add real Home Assistant service/authentication tests and optimizer takeover cases before, during and after service dispatch, alongside the existing protocol and dashboard attribute checks.

Requires Home Assistant 2025.3 or newer. Update the controller alongside the bridge so mid-dispatch takeover yields cleanly. Restart Home Assistant and refresh an imported storage-mode dashboard for the new diagnostic rows; YAML-mode dashboards referencing packaged files follow package updates. Existing entity IDs and custom configuration are preserved.

No physical inverter or live installation was connected. Matching register read-back establishes command acceptance, not physical charging. Test at a low rate and check measured Battery Power/Status and any pause reason. This release does not bypass inverter/BMS interlocks.
