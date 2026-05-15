"""Tests for the Terraform remote state bootstrap (MEJORAS.md §1.1)."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aetherdeploy.terraform import state_backend


def _stub_clients():
    s3 = MagicMock()
    dynamodb = MagicMock()
    return s3, dynamodb


def test_ensure_skipped_when_boto3_missing():
    def raise_import(name, *args, **kwargs):
        raise ImportError("boto3 not installed")

    with patch.object(state_backend, "_aws_clients", side_effect=raise_import):
        result = state_backend.ensure_s3_state_backend("b", "us-east-1", "lock")
    assert result.bucket_status == "skipped"
    assert "boto3" in (result.skipped_reason or "")
    assert result.ok


def test_creates_bucket_and_lock_table_when_absent():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.side_effect = Exception("404")
    dynamodb.describe_table.side_effect = Exception("ResourceNotFoundException")

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        result = state_backend.ensure_s3_state_backend("my-bucket", "eu-west-1", "my-lock")

    assert result.bucket_status == "created"
    assert result.lock_status == "created"
    assert result.ok

    s3.create_bucket.assert_called_once()
    call_kwargs = s3.create_bucket.call_args.kwargs
    assert call_kwargs["Bucket"] == "my-bucket"
    assert call_kwargs["CreateBucketConfiguration"] == {"LocationConstraint": "eu-west-1"}
    s3.put_bucket_versioning.assert_called_once()
    s3.put_bucket_encryption.assert_called_once()
    s3.put_public_access_block.assert_called_once()
    dynamodb.create_table.assert_called_once()


def test_us_east_1_omits_location_constraint():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.side_effect = Exception("404")
    dynamodb.describe_table.return_value = {"Table": {"TableName": "x"}}

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        state_backend.ensure_s3_state_backend("b", "us-east-1", "x")

    call_kwargs = s3.create_bucket.call_args.kwargs
    assert "CreateBucketConfiguration" not in call_kwargs


def test_existing_bucket_is_reused():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.return_value = {}
    dynamodb.describe_table.return_value = {"Table": {"TableName": "lock"}}

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        result = state_backend.ensure_s3_state_backend("b", "us-east-1", "lock")

    assert result.bucket_status == "exists"
    assert result.lock_status == "exists"
    s3.create_bucket.assert_not_called()
    dynamodb.create_table.assert_not_called()
    # Hardening is still asserted on a pre-existing bucket — defense in depth.
    s3.put_public_access_block.assert_called_once()


def test_bucket_creation_failure_is_reported_as_error():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.side_effect = Exception("404")
    s3.create_bucket.side_effect = Exception("AccessDenied")

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        result = state_backend.ensure_s3_state_backend("b", "us-east-1", "lock")

    assert result.bucket_status == "error"
    assert any("AccessDenied" in e for e in result.errors)
    assert not result.ok
    # Should short-circuit before touching DynamoDB on bucket failure.
    dynamodb.create_table.assert_not_called()


def test_lock_table_failure_is_separate_from_bucket():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.side_effect = Exception("404")
    dynamodb.describe_table.side_effect = Exception("NotFound")
    dynamodb.create_table.side_effect = Exception("LimitExceeded")

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        result = state_backend.ensure_s3_state_backend("b", "us-east-1", "lock")

    assert result.bucket_status == "created"
    assert result.lock_status == "error"
    assert not result.ok


def test_no_lock_table_when_none_requested():
    s3, dynamodb = _stub_clients()
    s3.head_bucket.return_value = {}

    with patch.object(state_backend, "_aws_clients", return_value=(s3, dynamodb)):
        result = state_backend.ensure_s3_state_backend("b", "us-east-1", lock_table=None)

    assert result.lock_status == "skipped"
    dynamodb.describe_table.assert_not_called()
    dynamodb.create_table.assert_not_called()


def test_dispatcher_handles_unknown_backend():
    result = state_backend.ensure_state_backend({"backend": "gcs", "bucket": "x", "region": "us"})
    assert result.bucket_status == "skipped"
    assert "not implemented" in (result.skipped_reason or "")


def test_dispatcher_routes_s3():
    with patch.object(state_backend, "ensure_s3_state_backend") as fn:
        state_backend.ensure_state_backend(
            {"backend": "s3", "bucket": "b", "region": "us-east-1", "dynamodb_table": "lock"}
        )
    fn.assert_called_once_with(bucket="b", region="us-east-1", lock_table="lock")
