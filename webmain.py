"""Container entry point — serves the web UI with uvicorn."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("CHANRIPPER_HOST", "0.0.0.0")
    port = int(os.environ.get("CHANRIPPER_PORT", "4848"))
    level = os.environ.get("CHANRIPPER_LOG", "info")
    uvicorn.run("web.app:app", host=host, port=port, log_level=level,
                access_log=False, proxy_headers=True)


if __name__ == "__main__":
    main()
