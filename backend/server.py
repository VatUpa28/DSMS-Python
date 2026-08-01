import os

from waitress import serve

from app import app


def main():
    host = os.environ.get("DSMS_HOST", "127.0.0.1")
    port = int(os.environ.get("DSMS_PORT", "8000"))
    threads = int(os.environ.get("DSMS_THREADS", "8"))

    print(f"Starting DSMS at http://{host}:{port}")

    serve(
        app,
        host=host,
        port=port,
        threads=threads,
    )


if __name__ == "__main__":
    main()