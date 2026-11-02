"""Start Mark 1: python run.py  (then open http://127.0.0.1:8000)"""
import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.getenv("MARK1_HOST", "127.0.0.1")
    port = int(os.getenv("MARK1_PORT", "8000"))
    print(f"Mark 1 (DEMO) -> http://{host}:{port}")
    uvicorn.run("app.server:app", host=host, port=port, log_level="info")
