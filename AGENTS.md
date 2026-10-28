# CitrusPi project notes

## Raspberry Pi access

- Connect from this Mac with `ssh -A citruspi` (including SSH agent forwarding).
- The Pi's repository is `/home/alex/CitrusPi`.
- The systemd service is `citruspi`.
- The dashboard is at `http://citruspi.local/`.

## Requested deployments

Use the normal Git workflow: commit and push locally, pull on the Pi, restart
`citruspi`, and verify the dashboard plus fresh sensor and plug readings. Preserve
unrelated working changes and runtime data. Report the deployed commit and checks.
