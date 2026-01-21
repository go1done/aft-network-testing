"""
Authentication and Session Management
Supports both local (SAML/SSO) and AWS execution modes
"""

import boto3
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from models import ExecutionMode


class AuthConfig:
    """
    Handles authentication for both local and AWS execution.

    LOCAL mode: Uses SAML/SSO profiles - same role across all accounts.
                Profile format can be customized via profile_pattern.
    AWS mode:   Uses IAM role assumption from Lambda/CodeBuild.
    """

    def __init__(self,
                 mode: ExecutionMode = ExecutionMode.LOCAL,
                 profile_name: Optional[str] = None,
                 profile_pattern: Optional[str] = None,
                 role_name: str = "AWSAFTExecution",
                 region: str = "us-west-2"):
        """
        Initialize authentication configuration.

        Args:
            mode: Execution mode (local or AWS)
            profile_name: AWS CLI profile name for hub account (local mode)
            profile_pattern: Pattern for account profiles, e.g., "sso-{account_id}"
                           If None, uses profile_name for all accounts (SSO with same role)
            role_name: IAM role to assume in target accounts (AWS mode only)
            region: AWS region
        """
        self.mode = mode
        self.profile_name = profile_name
        self.profile_pattern = profile_pattern
        self.role_name = role_name
        self.region = region
        self._session_cache: Dict[str, Tuple[boto3.Session, datetime]] = {}

    def get_hub_session(self, fallback_account_id: str = None) -> boto3.Session:
        """
        Get session for hub/shared services account.

        Args:
            fallback_account_id: Account ID to use when profile_pattern is set
                                (no single hub profile available)

        Returns:
            boto3.Session for hub account
        """
        if self.mode == ExecutionMode.LOCAL:
            if self.profile_name:
                # Single profile for all accounts
                return boto3.Session(
                    profile_name=self.profile_name,
                    region_name=self.region
                )
            elif self.profile_pattern and fallback_account_id:
                # Per-account profiles - use the fallback account
                return self.get_account_session(fallback_account_id)
            else:
                raise ValueError(
                    "Local mode requires either --profile or --profile-pattern with accounts"
                )
        else:
            # In AWS, use default credentials (instance/lambda/codebuild role)
            return boto3.Session(region_name=self.region)

    def uses_profile_pattern(self) -> bool:
        """Check if using per-account profile pattern."""
        return self.profile_pattern is not None and self.profile_name is None

    def get_account_session(self, account_id: str) -> boto3.Session:
        """
        Get session for a specific account.

        In LOCAL mode with SAML/SSO: Uses the same profile (SSO role has access to all accounts)
        In AWS mode: Assumes role in target account.

        Args:
            account_id: Target AWS account ID

        Returns:
            boto3.Session for the target account
        """
        # Check cache first
        cache_key = f"{account_id}"
        if cache_key in self._session_cache:
            cached_session, expiry = self._session_cache[cache_key]
            if datetime.utcnow() < expiry:
                return cached_session

        if self.mode == ExecutionMode.LOCAL:
            # SAML/SSO mode - use profile directly
            session = self._get_sso_session(account_id)
        else:
            # AWS mode - assume role
            session = self._assume_role_session(account_id)

        # Cache the session
        expiry = datetime.utcnow() + timedelta(minutes=50)
        self._session_cache[cache_key] = (session, expiry)

        return session

    def _get_sso_session(self, account_id: str) -> boto3.Session:
        """
        Get SSO session for account.

        If profile_pattern is set, uses pattern to determine profile name.
        Otherwise, uses the same profile_name (SSO role spans all accounts).
        """
        if self.profile_pattern:
            # Use pattern like "sso-{account_id}" or "profile-{account_id}"
            profile = self.profile_pattern.format(account_id=account_id)
        else:
            # Same SSO profile for all accounts (role has cross-account access)
            profile = self.profile_name

        return boto3.Session(
            profile_name=profile,
            region_name=self.region
        )

    def _assume_role_session(self, account_id: str) -> boto3.Session:
        """
        Assume role in target account (AWS mode).
        """
        hub_session = self.get_hub_session()
        sts = hub_session.client('sts')

        role_arn = f"arn:aws:iam::{account_id}:role/{self.role_name}"
        session_name = f"aft-test-{int(datetime.utcnow().timestamp())}"

        try:
            response = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                DurationSeconds=3600
            )

            return boto3.Session(
                aws_access_key_id=response['Credentials']['AccessKeyId'],
                aws_secret_access_key=response['Credentials']['SecretAccessKey'],
                aws_session_token=response['Credentials']['SessionToken'],
                region_name=self.region
            )

        except Exception as e:
            raise Exception(f"Failed to assume role {role_arn}: {str(e)}")

    def assume_role_session(self, account_id: str, session_name: Optional[str] = None) -> boto3.Session:
        """
        Backward compatible method - delegates to get_account_session.
        """
        return self.get_account_session(account_id)

    def clear_session_cache(self):
        """Clear cached sessions (useful for testing)"""
        self._session_cache.clear()