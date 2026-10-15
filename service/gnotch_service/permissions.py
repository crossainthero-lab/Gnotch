"""Permission catalogue.

`enforced` is the honest part: only permissions the *host* mediates (every call an
addon makes through the host API) are enforced. Addon backends are normal
processes running as the user; permissions such as `network` or `filesystem` are
declarations the user consents to, NOT a sandbox.
"""

CATALOGUE = {
    "notifications":  (True,  "Show notifications through Gnotch"),
    "activities":     (True,  "Show live activities in the notch"),
    "storage":        (True,  "Store small key/value data managed by Gnotch"),
    "addons.call":    (True,  "Call methods exposed by other addons (qualified: addons.call:<addon-id>)"),
    "env":            (True,  "Receive one environment variable from your session (qualified: env:<NAME>)"),
    "audio.record":   (False, "Record from the microphone (declared; not isolated)"),
    "network":        (False, "Access the network (declared; not isolated)"),
    "process.spawn":  (False, "Start other programs on your behalf (declared; not isolated)"),
    "filesystem":     (False, "Read/write files outside its own data directory (declared; not isolated)"),
}


def is_known_permission(p):
    base = p.split(":", 1)[0]
    return base in CATALOGUE and (":" not in p or base in ("addons.call", "env")) \
        and not (base == "env" and ":" not in p)


def describe(p):
    base = p.split(":", 1)[0]
    enforced, text = CATALOGUE.get(base, (False, "Unknown permission"))
    if ":" in p:
        text = f"{text.split(' (')[0]}: {p.split(':', 1)[1]}"
    return {"id": p, "enforced": enforced, "description": text}


def allows(granted, wanted):
    """`wanted` e.g. 'storage' or 'addons.call:org.x.y'. A bare 'addons.call' grant
    does not imply anything; qualified grants are exact."""
    return wanted in granted
