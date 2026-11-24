import json
from pathlib import Path


panels = [
    (
        "Nginx availability",
        "nginx_up",
        "stat",
    ),
    (
        "Exporter availability",
        'up{job="nginx"}',
        "stat",
    ),
    (
        "Requests per second",
        "rate(nginx_http_requests_total[1m])",
        "timeseries",
    ),
    (
        "Active connections",
        "nginx_connections_active",
        "timeseries",
    ),
    (
        "Dropped connections / minute",
        (
            "increase(nginx_connections_accepted[1m])"
            " - increase(nginx_connections_handled[1m])"
        ),
        "timeseries",
    ),
    (
        "Firing alerts",
        'ALERTS{alertstate="firing"}',
        "timeseries",
    ),
]

dashboard = {
    "uid": "lab6-nginx",
    "title": "Lab 6 - Nginx",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5s",
    "time": {
        "from": "now-15m",
        "to": "now",
    },
    "panels": [],
}

for index, (title, expr, panel_type) in enumerate(panels):
    dashboard["panels"].append(
        {
            "id": index + 1,
            "title": title,
            "type": panel_type,

            "datasource": {
                "type": "prometheus",
                "uid": "prometheus",
            },

            "gridPos": {
                "x": (index % 2) * 12,
                "y": (index // 2) * 8,
                "w": 12,
                "h": 8,
            },

            "targets": [
                {
                    "refId": "A",
                    "expr": expr,
                }
            ],

            "fieldConfig": {
                "defaults": {
                    "min": 0,
                },
                "overrides": [],
            },

            "options": {},
        }
    )

path = (
    Path(__file__).parent
    / "grafana"
    / "dashboards"
    / "lab6.json"
)

path.parent.mkdir(parents=True, exist_ok=True)

path.write_text(
    json.dumps(dashboard, indent=2) + "\n"
)