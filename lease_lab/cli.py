from __future__ import annotations

import argparse

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="lease-lab")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument("--database", default="lease_lab.db")
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
