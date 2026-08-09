"""Tests for canonical HMAC request signing."""

from assistant_core.auth.hmac import canonical_request, sign_request, verify_request


def test_canonical_request_has_expected_wire_format() -> None:
    """Canonical requests preserve the exact signed values."""
    assert canonical_request("post", "/events?type=test", "1723230000", b"hello") == (
        "POST\n/events?type=test\n1723230000\n"
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_signature_round_trip_verifies() -> None:
    """A signature verifies against its original request."""
    secret = "a" * 32
    signature = sign_request(secret, "POST", "/events", "1723230000", b'{"id":1}')

    assert verify_request(secret, signature, "POST", "/events", "1723230000", b'{"id":1}')


def test_signature_rejects_modified_body() -> None:
    """Changing the body invalidates a signature."""
    secret = "a" * 32
    signature = sign_request(secret, "POST", "/events", "1723230000", b'{"id":1}')

    assert not verify_request(secret, signature, "POST", "/events", "1723230000", b'{"id":2}')


def test_signature_is_sensitive_to_method_and_path() -> None:
    """Changing either method or path invalidates a signature."""
    secret = "a" * 32
    signature = sign_request(secret, "POST", "/events", "1723230000", b"payload")

    assert not verify_request(secret, signature, "GET", "/events", "1723230000", b"payload")
    assert not verify_request(secret, signature, "POST", "/other", "1723230000", b"payload")
