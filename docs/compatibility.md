# API compatibility policy

* The API version is `MAJOR.MINOR` (host: see `service/gnotch_service/__init__.py`, currently **1.0**).
* An addon declares the API it was written against in `api`.
* **MINOR** releases only add: new host methods, view node types/props, events, permissions. Existing behaviour does not change. An addon with `api: "1.2"` needs a host ≥ 1.2; a host ≥ the addon's minor loads it.
* **MAJOR** releases may break. A host never loads an addon with a different major; it is shown as *incompatible* with the reason, never silently disabled or crashed.
* Unknown view node types and unknown events are ignored, so newer addons degrade rather than fail where possible.
* Deprecations are announced one MINOR release ahead and removed no earlier than the next MAJOR.
* The wire protocol (`docs/protocol.md`) and manifest schema are part of the contract.
