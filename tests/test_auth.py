"""
Tests for authentication module.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

from auth import AuthConfig
from models import ExecutionMode


class TestAuthConfigInit:
    """Test AuthConfig initialization."""

    def test_init_local_mode_with_profile(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
            region="us-west-2",
        )
        assert auth.mode == ExecutionMode.LOCAL
        assert auth.profile_name == "test-profile"
        assert auth.profile_pattern is None
        assert auth.region == "us-west-2"

    def test_init_local_mode_with_profile_pattern(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_pattern="{account_id}",
            region="us-east-1",
        )
        assert auth.profile_pattern == "{account_id}"
        assert auth.profile_name is None

    def test_init_aws_mode(self):
        auth = AuthConfig(
            mode=ExecutionMode.AWS_LAMBDA,
            role_name="CustomRole",
        )
        assert auth.mode == ExecutionMode.AWS_LAMBDA
        assert auth.role_name == "CustomRole"

    def test_init_defaults(self):
        auth = AuthConfig()
        assert auth.mode == ExecutionMode.LOCAL
        assert auth.role_name == "AWSAFTExecution"
        assert auth.region == "us-west-2"


class TestAuthConfigUsesProfilePattern:
    """Test profile pattern detection."""

    def test_uses_profile_pattern_true(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_pattern="{account_id}",
        )
        assert auth.uses_profile_pattern() is True

    def test_uses_profile_pattern_false_with_profile_name(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
        )
        assert auth.uses_profile_pattern() is False

    def test_uses_profile_pattern_false_when_both_set(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
            profile_pattern="{account_id}",
        )
        # profile_name takes precedence
        assert auth.uses_profile_pattern() is False


class TestAuthConfigGetHubSession:
    """Test hub session retrieval."""

    @patch('auth.boto3.Session')
    def test_get_hub_session_local_with_profile(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
            region="us-east-1",
        )
        session = auth.get_hub_session()
        mock_session.assert_called_once_with(
            profile_name="test-profile",
            region_name="us-east-1",
        )

    @patch('auth.boto3.Session')
    def test_get_hub_session_aws_mode(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.AWS_LAMBDA,
            region="us-west-2",
        )
        session = auth.get_hub_session()
        mock_session.assert_called_once_with(region_name="us-west-2")

    def test_get_hub_session_local_no_profile_raises(self):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            # No profile_name or profile_pattern
        )
        with pytest.raises(ValueError, match="Local mode requires"):
            auth.get_hub_session()


class TestAuthConfigGetAccountSession:
    """Test account session retrieval."""

    @patch('auth.boto3.Session')
    def test_get_account_session_local_with_profile(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
            region="us-east-1",
        )
        session = auth.get_account_session("123456789012")
        # Should use same profile for all accounts in SSO mode
        mock_session.assert_called_with(
            profile_name="test-profile",
            region_name="us-east-1",
        )

    @patch('auth.boto3.Session')
    def test_get_account_session_local_with_pattern(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_pattern="acct-{account_id}",
            region="us-east-1",
        )
        session = auth.get_account_session("123456789012")
        mock_session.assert_called_with(
            profile_name="acct-123456789012",
            region_name="us-east-1",
        )

    @patch('auth.boto3.Session')
    def test_get_account_session_caching(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
            region="us-east-1",
        )
        # First call
        session1 = auth.get_account_session("123456789012")
        # Second call should use cache
        session2 = auth.get_account_session("123456789012")

        # Session constructor should only be called once due to caching
        assert mock_session.call_count == 1

    @patch('auth.boto3.Session')
    def test_get_account_session_different_accounts(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_pattern="{account_id}",
            region="us-east-1",
        )
        session1 = auth.get_account_session("111111111111")
        session2 = auth.get_account_session("222222222222")

        # Should create separate sessions for different accounts
        assert mock_session.call_count == 2


class TestAuthConfigAssumeRole:
    """Test role assumption in AWS mode."""

    @patch('auth.boto3.Session')
    def test_assume_role_session(self, mock_session_class):
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'AKIATEST',
                'SecretAccessKey': 'secret',
                'SessionToken': 'token',
            }
        }
        mock_session.client.return_value = mock_sts
        mock_session_class.return_value = mock_session

        auth = AuthConfig(
            mode=ExecutionMode.AWS_LAMBDA,
            role_name="TestRole",
            region="us-east-1",
        )

        # Get hub session first
        auth._hub_session = mock_session

        # Now get account session which triggers assume_role
        session = auth._assume_role_session("123456789012")

        mock_sts.assume_role.assert_called_once()
        call_args = mock_sts.assume_role.call_args
        assert "arn:aws:iam::123456789012:role/TestRole" in str(call_args)


class TestAuthConfigClearCache:
    """Test session cache clearing."""

    @patch('auth.boto3.Session')
    def test_clear_session_cache(self, mock_session):
        auth = AuthConfig(
            mode=ExecutionMode.LOCAL,
            profile_name="test-profile",
        )

        # Populate cache
        auth.get_account_session("111111111111")
        auth.get_account_session("222222222222")

        # Clear cache
        auth.clear_session_cache()

        # Cache should be empty
        assert len(auth._session_cache) == 0
