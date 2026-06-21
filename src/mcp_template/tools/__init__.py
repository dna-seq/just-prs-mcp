"""Tool registration modules, grouped by tier.

- ``recipes``      — essentials, present in every mode (no API key needed).
- ``extended``     — extra tools, registered only when mode == "extended".
- ``bakery_cloud`` — key-gated tools, always listed, auth enforced per call.
"""
