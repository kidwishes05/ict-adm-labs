import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
ca = root / "certs" / "ca.crt"

volumes = [
    "/cache",
    (
        f"{ca}:"
        "/etc/docker/certs.d/registry.lab.test/ca.crt:ro"
    ),
]

text = """
[[runners]]
  limit = 1

  [runners.docker]
    privileged = true
    network_mode = "lab6-net"
    volumes = VOLUMES
""".strip()

text = text.replace(
    "VOLUMES",
    json.dumps(volumes),
)

path = root / "runners" / "static" / "template.toml"

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(text + "\n")