"""Package-level smoke tests."""

import assistant_core


def test_package_exposes_initial_version() -> None:
    """The package exposes its initial semantic version."""
    assert assistant_core.__version__ == "0.1.0"
