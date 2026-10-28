# Offline soundbar reliability tests

From the repository root, with Python 3.12 or newer:

```sh
python -m pip install aiohttp==3.13.2 async-timeout==5.0.1 voluptuous==0.15.2
python -m unittest discover -s tests -v
```

No credentials, soundbar or Home Assistant installation are required. Cloud
device/API/coordinator classes and the local client load from this checkout;
HTTP and device operations are mocked. Home Assistant framework classes are
test doubles. Options-flow tests execute the original source AST against a
framework double with a read-only `config_entry`, and check the schema with
real voluptuous. This is regression coverage, not a full HA integration test.

The suite checks that first/subsequent polls and forced local discovery never
select modes, temporary failures do not permanently disable execute features,
polling remains throttled, off/unknown power is skipped, cached/reported modes
are retained, and explicit user selections still send the intended commands.
It also covers optional media metadata and options-flow creation/editing.
