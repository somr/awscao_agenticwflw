# PAY-DEMO-001 demonstration records

These are the durable records produced by a live Planning and Delivery run. They were
moved here from `.agentic-sdlc/records/PAY-DEMO-001/`. The workflows now write and read
records for real tickets under `sdlc-records/<ticket>/`.

The JSON manifests still contain the original `.agentic-sdlc/records/PAY-DEMO-001/...` paths.
They are approval-bound evidence and were deliberately not rewritten. To re-run Delivery for
this ticket, copy this directory to `sdlc-records/PAY-DEMO-001/`.

This Delivery run used the original single-implementer path: its manifest predates the
`implementation_mode` field. Delivery now defaults to `hybrid`, so reproduce the original
behaviour with `--input implementation_mode=single`; the default additionally needs the
`sdlc_code_supervisor` profile installed.
