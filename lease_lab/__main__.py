import argparse

import uvicorn

from .app import create_app, seed_demo


def main() -> None:
    parser = argparse.ArgumentParser(prog="lease_lab", description="租约语义演算器")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default="lease_lab.db")
    args = parser.parse_args()

    app = create_app(args.db)
    seed_demo(app.state.store)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
