"""persfin package."""

__version__ = "0.1.0"


def run_server() -> None:
    """Run the server."""
    from persfin.main import main

    main()
