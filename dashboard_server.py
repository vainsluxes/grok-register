#!/usr/bin/env python3
"""Launch Grok Manager web dashboard.

Usage:
  ./dashboard_server.py
  ./dashboard_server.py --host 0.0.0.0 --port 8787
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Grok Manager dashboard")
    parser.add_argument("--host", default=os.environ.get("GROK_DASH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GROK_DASH_PORT", "8787")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn/fastapi missing. Install with:")
        print(f"  uv pip install --python {ROOT}/.venv/bin/python fastapi 'uvicorn[standard]'")
        sys.exit(1)

    print(f"[*] Grok Manager → http://{args.host}:{args.port}")
    print(f"[*] project: {ROOT}")
    uvicorn.run(
        "dashboard.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=False,
    )


if __name__ == "__main__":
    main()
