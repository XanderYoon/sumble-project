"""HTTP application factory; importing this module starts no services."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Construct search, health, readiness, and documentation routes.

    Returns:
        An application with bounded admission/body handling and safe error logging.

    Raises:
        ValueError: Deployment configuration is invalid.
        NotImplementedError: Routes are not implemented in the skeleton.
    """
    raise NotImplementedError("HTTP routes are scheduled for implementation.")
