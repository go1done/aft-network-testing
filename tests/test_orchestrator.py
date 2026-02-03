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


class TestOrchestratorExportTestPlan:
    """Test test plan export functionality."""

    def test_export_test_plan_creates_yaml_file(self, tmp_path, sample_golden_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path

        test_plan_file = tmp_path / "test_plan.yaml"
        result = orchestrator.export_test_plan(str(test_plan_file))

        assert test_plan_file.exists()
        assert result['tests_exported'] > 0

    def test_export_test_plan_filter_only_active(self, tmp_path):
        """Test filtering to only active (traffic observed) patterns."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': True,
                        'ports_observed': [443],
                    },
                    {
                        'source_vpc_id': 'vpc-3',
                        'source_account_name': 'account-3',
                        'dest_vpc_id': 'vpc-4',
                        'dest_account_name': 'account-4',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': False,  # No traffic observed
                        'ports_observed': [],
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        result = orchestrator.export_test_plan(str(test_plan_file), only_active=True)

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should only have tests from the active pattern
        assert all('account-1' in t['source_account'] or 'account-2' in t['dest_account']
                   for t in plan['tests'])
        assert not any('account-3' in t['source_account'] for t in plan['tests'])

    def test_export_test_plan_filter_ports(self, tmp_path):
        """Test filtering to specific ports based on ports_allowed from security groups."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': True,
                        'ports_observed': [22, 443, 3306, 5432],
                        'ports_allowed': [22, 443, 3306, 5432],  # From security groups
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        result = orchestrator.export_test_plan(str(test_plan_file), ports=[443, 22])

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should have protocol-level test + only 443 and 22 port tests (intersection with ports_allowed)
        port_tests = [t for t in plan['tests'] if t['port'] is not None]
        ports_in_plan = {t['port'] for t in port_tests}
        assert ports_in_plan == {443, 22}
        assert 3306 not in ports_in_plan
        assert 5432 not in ports_in_plan

    def test_export_test_plan_filter_connection_types(self, tmp_path):
        """Test filtering by connection type."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': True,
                        'ports_observed': [443],
                    },
                    {
                        'source_vpc_id': 'vpc-3',
                        'source_account_name': 'account-3',
                        'dest_vpc_id': 'vpc-4',
                        'dest_account_name': 'account-4',
                        'connection_type': 'pcx',
                        'connection_id': 'pcx-456',
                        'expected_reachable': True,
                        'traffic_observed': True,
                        'ports_observed': [443],
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        result = orchestrator.export_test_plan(str(test_plan_file), connection_types=['tgw'])

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should only have TGW tests
        assert all(t['connection_type'] == 'tgw' for t in plan['tests'])

    def test_export_test_plan_connection_type_aliases(self, tmp_path):
        """Test that user-friendly connection type names are mapped to enum values."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'pcx',  # enum value, not 'peering'
                        'connection_id': 'pcx-123',
                        'expected_reachable': True,
                        'traffic_observed': False,
                        'ports_allowed': [443],
                    },
                    {
                        'source_vpc_id': 'vpc-3',
                        'source_account_name': 'account-3',
                        'dest_vpc_id': 'vpc-4',
                        'dest_account_name': 'account-4',
                        'connection_type': 'vpce',  # enum value, not 'privatelink'
                        'connection_id': 'vpce-456',
                        'expected_reachable': True,
                        'traffic_observed': False,
                        'ports_allowed': [443],
                    },
                    {
                        'source_vpc_id': 'vpc-5',
                        'source_account_name': 'account-5',
                        'dest_vpc_id': 'vpc-6',
                        'dest_account_name': 'account-6',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-789',
                        'expected_reachable': True,
                        'traffic_observed': False,
                        'ports_allowed': [443],
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        # Use user-friendly name 'peering' which should match 'pcx' in golden path
        result = orchestrator.export_test_plan(str(test_plan_file), connection_types=['peering'])

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should only have peering (pcx) tests
        assert len(plan['tests']) == 1
        assert plan['tests'][0]['connection_type'] == 'pcx'

    def test_export_test_plan_test_ports(self, tmp_path):
        """Test that test_ports generates port tests even without ports_allowed."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': False,
                        'ports_observed': [],
                        'ports_allowed': [],  # No ports_allowed
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        # Use test_ports to generate port tests regardless of ports_allowed
        result = orchestrator.export_test_plan(str(test_plan_file), test_ports=[443, 22])

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should have 2 port tests (443, 22) - no protocol-level by default
        assert len(plan['tests']) == 2
        ports_in_plan = {t['port'] for t in plan['tests']}
        assert ports_in_plan == {443, 22}

    def test_export_test_plan_include_protocol_level(self, tmp_path):
        """Test including protocol-level tests for production readiness checks."""
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = {
            'connectivity': {
                'patterns': [
                    {
                        'source_vpc_id': 'vpc-1',
                        'source_account_name': 'account-1',
                        'dest_vpc_id': 'vpc-2',
                        'dest_account_name': 'account-2',
                        'connection_type': 'tgw',
                        'connection_id': 'tgw-123',
                        'expected_reachable': True,
                        'traffic_observed': False,
                        'ports_observed': [],
                        'ports_allowed': [443],
                    },
                ]
            }
        }

        test_plan_file = tmp_path / "test_plan.yaml"
        result = orchestrator.export_test_plan(str(test_plan_file), include_protocol_level=True)

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should have both protocol-level and port-specific tests
        protocol_tests = [t for t in plan['tests'] if t['protocol'] == '-1']
        port_tests = [t for t in plan['tests'] if t['port'] is not None]
        assert len(protocol_tests) == 1
        assert len(port_tests) == 1
        assert protocol_tests[0]['port'] is None
        assert port_tests[0]['port'] == 443

    def test_export_test_plan_structure(self, tmp_path, sample_golden_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path

        test_plan_file = tmp_path / "test_plan.yaml"
        orchestrator.export_test_plan(str(test_plan_file))

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        assert 'version' in plan
        assert 'generated_at' in plan
        assert 'tests' in plan
        assert isinstance(plan['tests'], list)

    def test_export_test_plan_test_fields(self, tmp_path, sample_golden_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path

        test_plan_file = tmp_path / "test_plan.yaml"
        orchestrator.export_test_plan(str(test_plan_file))

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        test = plan['tests'][0]
        assert 'id' in test
        assert 'enabled' in test
        assert test['enabled'] is True
        assert 'source_vpc' in test
        assert 'source_account' in test
        assert 'dest_vpc' in test
        assert 'dest_account' in test
        assert 'connection_type' in test
        assert 'connection_id' in test
        assert 'protocol' in test
        assert 'description' in test
        assert 'notes' in test

    def test_export_test_plan_no_golden_path(self, tmp_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)

        test_plan_file = tmp_path / "test_plan.yaml"

        with pytest.raises(ValueError, match="No golden path loaded"):
            orchestrator.export_test_plan(str(test_plan_file))

    def test_export_test_plan_includes_port_specific_tests(self, tmp_path, sample_golden_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.golden_path = sample_golden_path

        test_plan_file = tmp_path / "test_plan.yaml"
        orchestrator.export_test_plan(str(test_plan_file))

        with open(test_plan_file, 'r') as f:
            plan = yaml.safe_load(f)

        # Should have only port-specific tests by default (no protocol-level)
        protocols = [t['protocol'] for t in plan['tests']]
        assert '-1' not in protocols  # Protocol-level tests not included by default
        assert 'tcp' in protocols  # Port-specific tests from ports_allowed


class TestOrchestratorRunFromTestPlan:
    """Test running tests from a test plan file."""

    @patch('orchestrator.ReachabilityTester')
    def test_run_from_test_plan_executes_enabled_tests(self, mock_tester_class, tmp_path):
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

        # Create a test plan file
        test_plan = {
            'version': '1.0',
            'generated_at': '2024-01-01T00:00:00',
            'tests': [
                {
                    'id': 'test-001',
                    'enabled': True,
                    'source_vpc': 'vpc-hub123',
                    'source_account': 'network-hub',
                    'dest_vpc': 'vpc-prod456',
                    'dest_account': 'prod-app',
                    'connection_type': 'tgw',
                    'connection_id': 'tgw-xyz789',
                    'protocol': '-1',
                    'port': None,
                    'description': 'Test connectivity',
                    'notes': '',
                },
            ],
        }
        test_plan_file = tmp_path / "test_plan.yaml"
        with open(test_plan_file, 'w') as f:
            yaml.dump(test_plan, f)

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.tester = mock_tester

        summary = orchestrator.run_from_test_plan(str(test_plan_file))

        assert summary['total_tests'] == 1
        mock_tester.test_connectivity.assert_called_once()

    @patch('orchestrator.ReachabilityTester')
    def test_run_from_test_plan_skips_disabled_tests(self, mock_tester_class, tmp_path):
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

        test_plan = {
            'version': '1.0',
            'generated_at': '2024-01-01T00:00:00',
            'tests': [
                {
                    'id': 'test-001',
                    'enabled': False,  # Disabled
                    'source_vpc': 'vpc-hub123',
                    'source_account': 'network-hub',
                    'dest_vpc': 'vpc-prod456',
                    'dest_account': 'prod-app',
                    'connection_type': 'tgw',
                    'connection_id': 'tgw-xyz789',
                    'protocol': '-1',
                    'port': None,
                    'description': 'Test connectivity',
                    'notes': '',
                },
            ],
        }
        test_plan_file = tmp_path / "test_plan.yaml"
        with open(test_plan_file, 'w') as f:
            yaml.dump(test_plan, f)

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.tester = mock_tester

        summary = orchestrator.run_from_test_plan(str(test_plan_file))

        assert summary['total_tests'] == 0
        assert summary['skipped'] == 1
        mock_tester.test_connectivity.assert_not_called()

    def test_run_from_test_plan_file_not_found(self, tmp_path):
        mock_auth = MagicMock()
        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)

        with pytest.raises(FileNotFoundError):
            orchestrator.run_from_test_plan(str(tmp_path / "nonexistent.yaml"))

    @patch('orchestrator.ReachabilityTester')
    def test_run_from_test_plan_returns_summary(self, mock_tester_class, tmp_path):
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

        test_plan = {
            'version': '1.0',
            'generated_at': '2024-01-01T00:00:00',
            'tests': [
                {
                    'id': 'test-001',
                    'enabled': True,
                    'source_vpc': 'vpc-hub123',
                    'source_account': 'network-hub',
                    'dest_vpc': 'vpc-prod456',
                    'dest_account': 'prod-app',
                    'connection_type': 'tgw',
                    'connection_id': 'tgw-xyz789',
                    'protocol': 'tcp',
                    'port': 443,
                    'description': 'Test HTTPS',
                    'notes': 'User note here',
                },
            ],
        }
        test_plan_file = tmp_path / "test_plan.yaml"
        with open(test_plan_file, 'w') as f:
            yaml.dump(test_plan, f)

        orchestrator = AFTTestOrchestrator(auth_config=mock_auth)
        orchestrator.tester = mock_tester

        summary = orchestrator.run_from_test_plan(str(test_plan_file))

        assert 'phase' in summary
        assert summary['phase'] == 'test-plan'
        assert 'start_time' in summary
        assert 'end_time' in summary
        assert 'total_tests' in summary
        assert 'passed' in summary
        assert 'failed' in summary
        assert 'results' in summary
