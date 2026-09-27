"""Pre-encoded JSON, matching the static Rust endpoint's work."""
from flask import Flask, Response

app = Flask(__name__)
BODY = b'{"status":"ok"}'


@app.get("/health")
def health():
    return Response(BODY, content_type="application/json")
