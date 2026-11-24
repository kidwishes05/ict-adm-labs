import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, g, request
from prometheus_client import (Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST,)

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

# OpenTelemetry

resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "lab1-service")})

provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

if otel_endpoint:
    exporter = OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))

tracer = trace.get_tracer(__name__)

# Flask

app = Flask(__name__)

# Автоматический root span на каждый HTTP request.
FlaskInstrumentor().instrument_app(app)

# Prometheus metrics

REQUESTS = Counter("http_requests_total", "Total number of HTTP requests", ["method", "endpoint", "status"],)

ERRORS = Counter("http_errors_total", "Total number of HTTP errors", ["endpoint"],)

DURATION = Histogram("http_request_duration_seconds", "HTTP request duration in seconds", ["endpoint"],)

# Logging

def get_trace_id():
    span = trace.get_current_span()
    context = span.get_span_context()

    if context.is_valid:
        return format(context.trace_id, "032x")

    return None


def log_json(level, message):
    log = {"level": level, "message": message, "trace_id": get_trace_id(),}

    print(json.dumps(log), flush=True)

# Request metrics

@app.before_request
def before_request():
    g.started_at = time.time()


@app.after_request
def after_request(response):
    duration = time.time() - g.started_at

    REQUESTS.labels(method=request.method, endpoint=request.path, status=str(response.status_code),).inc()

    DURATION.labels(endpoint=request.path).observe(duration)

    return response

# Main page

@app.route("/")
def index():
    return """
    <!DOCTYPE html>

    <html>
    <head>
        <meta charset="UTF-8">
        <title>Monitoring Lab</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 100px auto;
                text-align: center;
            }

            button {
                padding: 15px 25px;
                margin: 10px;
                font-size: 17px;
                cursor: pointer;
            }

            #result {
                margin-top: 30px;
                font-size: 18px;
            }
        </style>
    </head>

    <body>

        <h1>Monitoring Lab</h1>
        <p>Generate test events for the monitoring stack.</p>

        <button onclick="callEndpoint('/error')">
            Cause an error
        </button>

        <button onclick="callEndpoint('/delay')">
            Cause a delay
        </button>

        <button onclick="callEndpoint('/load')">
            Load
        </button>

        <div id="result"></div>

        <script>
            async function callEndpoint(endpoint) {
                const output = document.getElementById("result");

                output.innerText = "Requesting " + endpoint + "...";

                try {
                    const response = await fetch(endpoint);
                    const text = await response.text();

                    output.innerText =
                        "HTTP " + response.status + ": " + text;
                }
                catch (error) {
                    output.innerText = error;
                }
            }
        </script>

    </body>
    </html>
    """

# Normal request

@app.route("/work")
def work():
    log_json("INFO", "Normal request processed")
    return "OK", 200

# Error

@app.route("/error")
def cause_error():
    ERRORS.labels(endpoint="/error").inc()

    exception = RuntimeError("Intentional test error")

    span = trace.get_current_span()

    span.record_exception(exception)
    span.set_status(Status(StatusCode.ERROR,"Intentional HTTP 500 error",))

    log_json("ERROR", "Intentional error generated",)

    return "Intentional error", 500

# Delay

@app.route("/delay")
def cause_delay():
    delay = random.uniform(1, 3)

    with tracer.start_as_current_span("slow-dependency"):
        log_json("INFO", f"Slow operation started: {delay:.2f}s",)
        time.sleep(delay)

    log_json("INFO", "Slow operation completed",)

    return f"Delayed for {delay:.2f} seconds", 200

# Load

def make_request():
    try:
        requests.get("http://127.0.0.1:8000/work",timeout=5,)
    except requests.RequestException:
        pass


@app.route("/load")
def generate_load():
    number_of_requests = 50

    log_json("INFO", f"Generating {number_of_requests} requests",)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request)
            for _ in range(number_of_requests)]

        for future in futures:
            future.result()

    return f"Generated {number_of_requests} requests", 200

# Prometheus metrics

@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST,)

# Start

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True,)