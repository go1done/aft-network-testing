"""
Tests for orchestrator module.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import yaml
import os

from orchestrator import AFTTestOrchestrator
from models import TestPhase, TestResult, AccountConfig
from auth import AuthConfig


class TestOrchestratorInit:
    """Test AFTTestOrchestrator initialization."""

    def test_init_with_defaults(self):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)

        assert orchestrator.auth == mock_auth
        assert orchestrator.golden_path_file == "./golden_path.yaml"
        assert orchestrator.s3_bucket is None
        assert orchestrator.golden_path is None

    def test_init_with_custom_paths(self):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(
            auth_config=mock_auth,
            golden_path_file="/custom/path.yaml",
            s3_bucket="my-bucket",
        )

        assert orchestrator.golden_path_file == "/custom/path.yaml"
        assert orchestrator.s3_bucket == "my-bucket"

    def test_init_loads_existing_golden_path(self, tmp_path, sample_golden_path):
        golden_path_file = tmp_path / "golden_path.yaml"
        with open(golden_path_file, 'w') as f:
            yaml.dump(sample_golden_path, f)

        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(
            auth_config=mock_auth,
            golden_path_file=str(golden_path_file),
        )

        assert orchestrator.golden_path is not None
        assert orchestrator.golden_path['version'] == '1.0'


class TestOrchestratorGenerateTestMatrix:
    """Test test matrix generation."""

    def test_generate_test_matrix_no_golden_path(self):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)

        test_cases = orchestrator.generate_test_matrix()

        assert len(test_cases) == 1
        assert test_cases[0]['protocol'] == '-1'
        assert test_cases[0]['name'] == 'Protocol-Level Connectivity'

    def test_generate_test_matrix_with_golden_path(self, sample_golden_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path

        test_cases = orchestrator.generate_test_matrix()

        # Should have at least protocol-level test
        assert any(tc['protocol'] == '-1' for tc in test_cases)
        # Should have port-specific tests from golden path patterns
        protocol_level = [tc for tc in test_cases if tc['protocol'] == '-1']
        assert len(protocol_level) >= 1


class TestOrchestratorRunTests:
    """Test test execution."""

    @patch('orchestrator.ReachabilityTester')
    @patch('orchestrator.publish_results')
    def test_run_tests_post_release(self, mock_publish, mock_tester_class, sample_accounts, sample_golden_path):
        from models import TestCase
        mock_auth = MagicMock()
        mock_tester = MagicMock()
        mock_tester.test_connectivity.return_value = TestCase(
            name="test",
            result=TestResult.PASS,
            message="passed",
            duration_ms=100,
        )
        mock_tester_class.return_value = mock_tester

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path
        orchestrator.tester = mock_tester

        summary = orchestrator.run_tests(
            accounts=sample_accounts,
            phase=TestPhase.POST_RELEASE,
            publish=False,
        )

        assert 'phase' in summary
        assert summary['phase'] == 'post-release'
        assert 'total_tests' in summary
        assert 'passed' in summary
        assert 'failed' in summary
        mock_publish.assert_not_called()  # publish=False

    @patch('orchestrator.ReachabilityTester')
    @patch('orchestrator.publish_results')
    def test_run_tests_with_publish(self, mock_publish, mock_tester_class, sample_accounts, sample_golden_path):
        from models import TestCase
        mock_auth = MagicMock()
        mock_auth.get_hub_session.return_value = MagicMock()
        mock_tester = MagicMock()
        mock_tester.test_connectivity.return_value = TestCase(
            name="test",
            result=TestResult.PASS,
            message="passed",
            duration_ms=100,
        )
        mock_tester_class.return_value = mock_tester

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path
        orchestrator.tester = mock_tester

        summary = orchestrator.run_tests(
            accounts=sample_accounts,
            phase=TestPhase.POST_RELEASE,
            publish=True,
        )

        mock_publish.assert_called_once()

    @patch('orchestrator.ReachabilityTester')
    def test_run_tests_pre_release_skips_tests(self, mock_tester_class, sample_accounts, sample_golden_path):
        mock_auth = MagicMock()
        mock_tester = MagicMock()
        mock_tester_class.return_value = mock_tester

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path
        orchestrator.tester = mock_tester

        summary = orchestrator.run_tests(
            accounts=sample_accounts,
            phase=TestPhase.PRE_RELEASE,
            publish=False,
        )

        # PRE_RELEASE should skip actual test execution
        mock_tester.test_connectivity.assert_not_called()
        assert summary['total_tests'] == 0


class TestOrchestratorDiscoverBaseline:
    """Test baseline discovery."""

    @patch('orchestrator.ConnectivityDiscovery')
    @patch('orchestrator.BaselineDiscovery')
    def test_discover_baseline(self, mock_baseline_class, mock_conn_class, sample_accounts, tmp_path):
        mock_auth = MagicMock()
        mock_hub_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {'Account': '111111111111'}
        mock_hub_session.client.return_value = mock_sts
        mock_auth.get_hub_session.return_value = mock_hub_session

        mock_baseline = MagicMock()
        mock_baseline.scan_all_accounts.return_value = []
        mock_baseline.generate_golden_path.return_value = {
            'version': '1.0',
            'account_baselines': [],
        }
        mock_baseline_class.return_value = mock_baseline

        mock_conn = MagicMock()
        mock_conn.build_connectivity_map.return_value = []
        mock_conn_class.return_value = mock_conn

        golden_path_file = tmp_path / "golden_path.yaml"
        orchestrator = AFTTestOrchestrator(
            auth_config=mock_auth,
            golden_path_file=str(golden_path_file),
        )

        result = orchestrator.discover_baseline(
            accounts=sample_accounts,
            tgw_id="tgw-test123",
        )

        assert 'connectivity' in result
        assert golden_path_file.exists()


class TestOrchestratorAliases:
    """Test backward compatibility aliases."""

    def test_discover_and_generate_golden_path_alias(self, sample_accounts):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.discover_baseline = MagicMock(return_value={'version': '1.0'})

        result = orchestrator.discover_and_generate_golden_path(sample_accounts)

        orchestrator.discover_baseline.assert_called_once_with(sample_accounts, None, None)

    def test_run_test_suite_alias(self, sample_accounts):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.run_tests = MagicMock(return_value={'phase': 'test'})

        result = orchestrator.run_test_suite(
            sample_accounts,
            TestPhase.POST_RELEASE,
            parallel=True,
            publish=True,
        )

        orchestrator.run_tests.assert_called_once_with(
            sample_accounts,
            TestPhase.POST_RELEASE,
            True,
            True,
        )
