"""
Tests for CLI module.
"""

import pytest
import sys
from unittest.mock import patch, MagicMock
from io import StringIO

from cli import parse_args, load_accounts, get_execution_mode
from models import ExecutionMode


class TestParseArgs:
    """Test CLI argument parsing."""

    def test_parse_minimal_args(self):
        with patch.object(sys, 'argv', ['cli', '--phase', 'discover']):
            args = parse_args()
            assert args.phase == 'discover'
            assert args.mode == 'local'  # default
            assert args.accounts_file == 'config/accounts.yaml'  # default

    def test_parse_all_args(self):
        with patch.object(sys, 'argv', [
            'cli',
            '--mode', 'aws',
            '--profile', 'test-profile',
            '--role', 'CustomRole',
            '--region', 'us-west-2',
            '--phase', 'post-release',
            '--accounts-file', 'custom/accounts.yaml',
            '--tgw-id', 'tgw-test123',
            '--connection-types', 'tgw,peering',
            '--golden-path', 'custom/golden.yaml',
            '--s3-bucket', 'my-bucket',
            '--publish-results',
            '--parallel',
            '--dry-run',
            '--verbose',
        ]):
            args = parse_args()
            assert args.mode == 'aws'
            assert args.profile == 'test-profile'
            assert args.role == 'CustomRole'
            assert args.region == 'us-west-2'
            assert args.phase == 'post-release'
            assert args.accounts_file == 'custom/accounts.yaml'
            assert args.tgw_id == 'tgw-test123'
            assert args.connection_types == 'tgw,peering'
            assert args.golden_path == 'custom/golden.yaml'
            assert args.s3_bucket == 'my-bucket'
            assert args.publish_results is True
            assert args.parallel is True
            assert args.dry_run is True
            assert args.verbose is True

    def test_parse_profile_pattern(self):
        with patch.object(sys, 'argv', [
            'cli',
            '--phase', 'discover',
            '--profile-pattern', '{account_id}',
        ]):
            args = parse_args()
            assert args.profile_pattern == '{account_id}'
            assert args.profile is None

    def test_phase_choices_valid(self):
        for phase in ['discover', 'pre-release', 'post-release']:
            with patch.object(sys, 'argv', ['cli', '--phase', phase]):
                args = parse_args()
                assert args.phase == phase

    def test_phase_invalid_choice(self):
        with patch.object(sys, 'argv', ['cli', '--phase', 'invalid']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_phase_pre_flight_removed(self):
        """Verify pre-flight phase is no longer valid."""
        with patch.object(sys, 'argv', ['cli', '--phase', 'pre-flight']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_mode_choices_valid(self):
        for mode in ['local', 'aws', 'codebuild']:
            with patch.object(sys, 'argv', ['cli', '--mode', mode, '--phase', 'discover']):
                args = parse_args()
                assert args.mode == mode

    def test_publish_results_default_false(self):
        with patch.object(sys, 'argv', ['cli', '--phase', 'discover']):
            args = parse_args()
            assert args.publish_results is False


class TestLoadAccounts:
    """Test account loading from YAML."""

    def test_load_accounts_valid_file(self, tmp_path):
        accounts_file = tmp_path / "accounts.yaml"
        accounts_file.write_text("""
accounts:
  - account_id: "111111111111"
    account_name: "test-account-1"
    region: "us-east-1"
  - account_id: "222222222222"
    account_name: "test-account-2"
    vpc_id: "vpc-abc123"
""")
        accounts = load_accounts(str(accounts_file))
        assert len(accounts) == 2
        assert accounts[0].account_id == "111111111111"
        assert accounts[0].account_name == "test-account-1"
        assert accounts[1].vpc_id == "vpc-abc123"

    def test_load_accounts_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_accounts("/nonexistent/path/accounts.yaml")

    def test_load_accounts_empty_file(self, tmp_path):
        accounts_file = tmp_path / "empty.yaml"
        accounts_file.write_text("")
        accounts = load_accounts(str(accounts_file))
        assert accounts == []

    def test_load_accounts_no_accounts_key(self, tmp_path):
        accounts_file = tmp_path / "invalid.yaml"
        accounts_file.write_text("other_key: value")
        accounts = load_accounts(str(accounts_file))
        assert accounts == []


class TestGetExecutionMode:
    """Test execution mode conversion."""

    def test_get_execution_mode_local(self):
        assert get_execution_mode('local') == ExecutionMode.LOCAL

    def test_get_execution_mode_aws(self):
        assert get_execution_mode('aws') == ExecutionMode.AWS_LAMBDA

    def test_get_execution_mode_codebuild(self):
        assert get_execution_mode('codebuild') == ExecutionMode.AWS_CODEBUILD

    def test_get_execution_mode_unknown_defaults_to_local(self):
        assert get_execution_mode('unknown') == ExecutionMode.LOCAL


class TestParseArgsTestPlan:
    """Test CLI argument parsing for test plan features."""

    def test_parse_export_test_plan_phase(self):
        with patch.object(sys, 'argv', [
            'cli',
            '--phase', 'export-test-plan',
            '--golden-path', 'golden_path.yaml',
            '--test-plan', 'test_plan.yaml',
        ]):
            args = parse_args()
            assert args.phase == 'export-test-plan'
            assert args.test_plan == 'test_plan.yaml'

    def test_parse_run_test_plan_phase(self):
        with patch.object(sys, 'argv', [
            'cli',
            '--phase', 'run-test-plan',
            '--test-plan', 'my_test_plan.yaml',
        ]):
            args = parse_args()
            assert args.phase == 'run-test-plan'
            assert args.test_plan == 'my_test_plan.yaml'

    def test_test_plan_default(self):
        with patch.object(sys, 'argv', ['cli', '--phase', 'discover']):
            args = parse_args()
            assert args.test_plan == './test_plan.yaml'
