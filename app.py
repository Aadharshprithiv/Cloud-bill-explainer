"""Cloud Bill Explainer - Flask web app.

Uploaded bills are processed in memory and never written to disk, stored or logged.
"""

import io
import os
from pathlib import Path

from flask import Flask, render_template, request
from flask.wrappers import Request
from werkzeug.exceptions import RequestEntityTooLarge

from analysis import analyze
from explain.templates import format_quantity, money
from parsers.cur_parser import MAX_BYTES, BillParseError

SAMPLE_BILL = Path(__file__).resolve().parent / "sample_data" / "sample_aws_bill.csv"


class InMemoryRequest(Request):
    """Keep uploaded files in memory instead of Werkzeug's temp files for large uploads."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return io.BytesIO()


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.request_class = InMemoryRequest
    app.config.update(
        # 20 MB file plus a little room for the rest of the form.
        MAX_CONTENT_LENGTH=MAX_BYTES + 64 * 1024,
        ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY", ""),
        ANTHROPIC_MODEL=os.environ.get("ANTHROPIC_MODEL", ""),
    )
    if config:
        app.config.update(config)

    app.add_template_filter(lambda amount, currency="USD": money(amount, currency), "money")
    app.add_template_filter(format_quantity, "quantity")
    app.add_template_filter(lambda d: f"{d.day} {d:%B %Y}", "nice_date")  # portable "1 October 2025"

    def show_error(message: str, status: int = 400):
        return render_template("index.html", error=message), status

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.post("/analyze")
    def analyze_upload():
        upload = request.files.get("bill")
        if upload is None or not upload.filename:
            return show_error("Please choose a CSV file to upload.")
        if not upload.filename.lower().endswith(".csv"):
            return show_error("Please upload a .csv file. If your bill was downloaded as a .zip or "
                              ".gz file, unzip it first and upload the .csv inside.")
        try:
            data = upload.read()
        finally:
            upload.close()
        return _render_results(data, sample=False)

    @app.get("/sample")
    def sample():
        return _render_results(SAMPLE_BILL.read_bytes(), sample=True)

    def _render_results(data: bytes, sample: bool):
        try:
            report = analyze(data)
        except BillParseError as exc:
            return show_error(exc.user_message)
        return render_template("results.html", report=report, sample=sample)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return show_error("That file is larger than 20 MB. Please upload one month of your bill at a time.", 413)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
