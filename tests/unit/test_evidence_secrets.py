"""The credential guard.

Two failure modes matter equally. Missing a real key writes a secret into a
committed file. Firing on ordinary text gets the guard disabled, after which it
protects nothing. Both directions are tested.
"""

from __future__ import annotations

import pytest
from skillkernel.core.errors import UnsafeOperationError
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.evidence.secrets import scan_bytes, scan_text

REAL_LOOKING = [
    ("aws-access-key-id", "AKIAIOSFODNN7EXAMPLE"),
    ("github-token", "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
    ("slack-token", "xoxb-123456789012-abcdefghijklmnop"),
    ("google-api-key", "AIza" + "S" * 35),
    ("anthropic-api-key", "sk-ant-api03-" + "x" * 32),
    ("private-key-block", "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n"),
    ("assigned-secret", 'api_key = "s3cr3tvalue_thatislong12345"'),
    ("assigned-secret", "client_secret: aVeryLongLookingSecretValue123"),
]

ORDINARY_TEXT = [
    "All 148 tests passed in 3.2s",
    "def compute_hash(payload: bytes) -> str: return sha256(payload).hexdigest()",
    "commit 0dc7a2419f4c8b7e6a5d3c2b1a0f9e8d7c6b5a4f",
    "The AnimationMixer advances according to the supplied delta.",
    "Set api_key to the value from your password manager before running.",
    "sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "PASS FAIL SKIPPED BLOCKED NOT RUN",
    "password: ****",
]


@pytest.mark.parametrize(("expected_pattern", "payload"), REAL_LOOKING)
def test_credential_shapes_are_detected(expected_pattern: str, payload: str) -> None:
    found = scan_text(payload)
    assert found, f"{expected_pattern} was not detected"
    assert expected_pattern in {match.pattern for match in found}


@pytest.mark.parametrize("payload", ORDINARY_TEXT)
def test_ordinary_engineering_text_does_not_trip_the_guard(payload: str) -> None:
    assert scan_text(payload) == []


def test_matches_are_located_by_offset_and_ordered() -> None:
    payload = "harmless prefix AKIAIOSFODNN7EXAMPLE trailing"
    (match,) = scan_text(payload)
    assert match.offset == payload.index("AKIA")
    assert "byte offset" in str(match)


def test_binary_content_is_scanned_leniently() -> None:
    payload = b"\x00\x01\xff binary preamble AKIAIOSFODNN7EXAMPLE \xfe"
    assert scan_bytes(payload)


def test_recording_an_artifact_containing_a_key_is_refused(ledger: EvidenceLedger) -> None:
    with pytest.raises(UnsafeOperationError, match="looks like it contains credentials"):
        ledger.record(
            kind="file",
            summary="build log",
            project="demo",
            source_type="command",
            source_detail="make",
            artifact_bytes=b"exporting AKIAIOSFODNN7EXAMPLE now",
            artifact_name="build.log",
        )


def test_a_key_in_the_record_summary_is_refused(ledger: EvidenceLedger) -> None:
    with pytest.raises(UnsafeOperationError, match="record metadata"):
        ledger.record(
            kind="command_output",
            summary="deployed with AKIAIOSFODNN7EXAMPLE",
            project="demo",
            source_type="command",
            source_detail="deploy",
        )


def test_a_key_in_the_record_attributes_is_refused(ledger: EvidenceLedger) -> None:
    with pytest.raises(UnsafeOperationError, match="record attributes"):
        ledger.record(
            kind="command_output",
            summary="a run",
            project="demo",
            source_type="command",
            source_detail="deploy",
            attributes={"env": {"token": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"}},
        )


def test_a_refused_record_does_not_leave_a_partial_entry(ledger: EvidenceLedger) -> None:
    """The guard runs before any identifier is allocated or file written."""
    with pytest.raises(UnsafeOperationError):
        ledger.record(
            kind="command_output",
            summary="deployed with AKIAIOSFODNN7EXAMPLE",
            project="demo",
            source_type="command",
            source_detail="deploy",
        )
    assert ledger.ids() == []
    assert ledger.verify() == []


def test_an_ordinary_artifact_records_successfully(ledger: EvidenceLedger) -> None:
    record = ledger.record(
        kind="command_output",
        summary="pytest run",
        project="demo",
        source_type="command",
        source_detail="python -m pytest -q",
        artifact_bytes=b"148 passed in 3.21s\n",
        artifact_name="pytest.log",
    )
    assert record.artifact is not None
