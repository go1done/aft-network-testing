"""
AFT Test Orchestrator
Thin orchestration layer that coordinates discovery, testing, and reporting.
"""

import os
import yaml
from typing import Dict, List
from dataclasses import asdict
from datetime import datetime

from models import (
    ExecutionMode,
    TestPhase,
    TestResult,
    AccountConfig,
)
from auth import AuthConfig

from reporting import publish_results, print_summary
from baseline import BaselineDiscovery
from reachability import ReachabilityTester


class AFTTestOrchestrator:
    """
    Main orchestrator - coordinates discovery, testing, and reporting.
    Works in both local and AWS modes.
    """

    def __init__(self,
                 auth_config: AuthConfig,
                 golden_path_file: str = None,
                 s3_bucket: str = None):
        """
        Initialize the orchestrator.

        Args:
            auth_config: AuthConfig instance for AWS authentication
            golden_path_file: Path to golden path YAML file
            s3_bucket: Optional S3 bucket for results storage
        """
        self.auth = auth_config
        self.golden_path_file = golden_path_file or "./golden_path.yaml"
        self.s3_bucket = s3_bucket

        # Initialize components
        self.discovery = BaselineDiscovery(auth_config=auth_config)
        self.tester = ReachabilityTester(auth_config=auth_config)

        # Load golden path if it exists
        self.golden_path = None
        if golden_path_file and os.path.exists(golden_path_file):
            with open(golden_path_file, 'r') as f:
                self.golden_path = yaml.safe_load(f)

    def discover_baseline(self, accounts: List[AccountConfig], tgw_id: str = None) -> Dict:
        """
        Phase 0: Discover baseline and generate golden path.

        Args:
            accounts: List of AccountConfig instances
            tgw_id: Optional Transit Gateway ID for connectivity discovery

        Returns:
            Generated golden path dictionary
        """
        print("=" * 80)
        print("PHASE 0: BASELINE DISCOVERY & GOLDEN PATH GENERATION")
        print("=" * 80)

        # Discover VPC configurations
        baselines = self.discovery.scan_all_accounts(accounts)
        golden_path = self.discovery.generate_golden_path(baselines)

        # Discover VPC-to-VPC connectivity if TGW provided
        if tgw_id:
            hub_account_id = self.auth.get_hub_session().client('sts').get_caller_identity()['Account']
            conn_discovery = ConnectivityDiscovery(
                auth_config=self.auth,
                hub_account_id=hub_account_id
            )

            # Convert AccountConfig to dict for connectivity discovery
            accounts_dict = [
                {
                    'account_id': acc.account_id,
                    'account_name': acc.account_name,
                    'vpc_id': acc.vpc_id
                }
                for acc in accounts
            ]

            connectivity_patterns = conn_discovery.build_connectivity_map(
                accounts_dict,
                tgw_id,
                use_flow_logs=True
            )

            golden_path['connectivity'] = {
                'patterns': [
                    {
                        'source_vpc_id': p.source_vpc_id,
                        'source_account_id': p.source_account_id,
                        'source_account_name': p.source_account_name,
                        'dest_vpc_id': p.dest_vpc_id,
                        'dest_account_id': p.dest_account_id,
                        'dest_account_name': p.dest_account_name,
                        'connection_type': p.connection_type.value,
                        'connection_id': p.connection_id,
                        'via_tgw': tgw_id,
                        'expected_reachable': p.expected,
                        'traffic_observed': p.traffic_observed,
                        'protocols_observed': list(p.protocols_observed),
                        'ports_observed': sorted(list(p.ports_observed)),
                        'use_case': p.use_case
                    }
                    for p in connectivity_patterns
                ],
                'tgw_id': tgw_id,
                'total_paths': len(connectivity_patterns),
                'active_paths': sum(1 for p in connectivity_patterns if p.traffic_observed)
            }

        # Save golden path
        with open(self.golden_path_file, 'w') as f:
            yaml.dump(golden_path, f, default_flow_style=False)

        print(f"\n✓ Golden path saved to {self.golden_path_file}")

        self.golden_path = golden_path
        return golden_path

    def generate_test_matrix(self, account: AccountConfig = None) -> List[Dict]:
        """
        Generate test cases based on golden path.

        Args:
            account: Optional AccountConfig for account-specific tests

        Returns:
            List of test case dictionaries
        """
        if not self.golden_path:
            print("Warning: No golden path loaded, using basic tests")
            return [
                {'protocol': '-1', 'port': None, 'name': 'Protocol-Level Connectivity'}
            ]

        test_cases = []

        # Always test protocol-level first
        test_cases.append({
            'protocol': '-1',
            'port': None,
            'name': 'Protocol-Level Connectivity'
        })

        # Add tests for discovered common patterns
        patterns = self.golden_path.get('expected_configuration', {}).get(
            'security', {}
        ).get('common_ingress_patterns', [])

        tested_ports = set()
        for pattern in patterns:
            if ':' in pattern:
                protocol, port_str = pattern.split(':', 1)
                port = int(port_str)

                if port not in tested_ports:
                    test_cases.append({
                        'protocol': protocol,
                        'port': port,
                        'name': f'{protocol.upper()} Port {port} (Golden Path)'
                    })
                    tested_ports.add(port)

        return test_cases

    def run_tests(self, accounts: List[AccountConfig], phase: TestPhase, parallel: bool = True) -> Dict:
        """
        Execute comprehensive test suite.

        Args:
            accounts: List of AccountConfig instances
            phase: Test phase (PRE_RELEASE, PRE_FLIGHT, POST_RELEASE)
            parallel: Whether to run tests in parallel (reserved for future use)

        Returns:
            Test summary dictionary
        """
        print(f"\n{'=' * 80}")
        print(f"PHASE: {phase.value.upper()}")
        print(f"{'=' * 80}")

        start_time = datetime.utcnow()
        all_results = []

        # Load connectivity patterns from golden path
        connectivity_tests = []
        if self.golden_path and 'connectivity' in self.golden_path:
            patterns = self.golden_path['connectivity'].get('patterns', [])

            for pattern in patterns:
                if not pattern.get('expected_reachable'):
                    continue

                # Protocol-level test
                connectivity_tests.append({
                    'source_vpc': pattern['source_vpc_id'],
                    'source_account': pattern['source_account_name'],
                    'dest_vpc': pattern['dest_vpc_id'],
                    'dest_account': pattern['dest_account_name'],
                    'tgw_id': pattern.get('via_tgw'),
                    'protocol': '-1',
                    'port': None
                })

                # Port-specific tests if traffic observed
                if pattern.get('traffic_observed'):
                    for port in pattern.get('ports_observed', []):
                        connectivity_tests.append({
                            'source_vpc': pattern['source_vpc_id'],
                            'source_account': pattern['source_account_name'],
                            'dest_vpc': pattern['dest_vpc_id'],
                            'dest_account': pattern['dest_account_name'],
                            'tgw_id': pattern.get('via_tgw'),
                            'protocol': 'tcp',
                            'port': port
                        })

        print(f"Generated {len(connectivity_tests)} connectivity tests from golden path")

        # Execute connectivity tests
        if phase != TestPhase.PRE_RELEASE:
            for test in connectivity_tests:
                print(
                    f"\nTesting: {test['source_account']} → {test['dest_account']} "
                    f"({test['protocol']}:{test.get('port', 'all')})"
                )

                result = self.tester.test_reachability(
                    source_vpc=test['source_vpc'],
                    dest_vpc=test['dest_vpc'],
                    tgw_id=test['tgw_id'],
                    protocol=test['protocol'],
                    port=test.get('port')
                )

                all_results.append(result)

                status_icon = "✓" if result.result == TestResult.PASS else "✗"
                print(f"  {status_icon} {result.name}: {result.message}")

        # Generate summary
        end_time = datetime.utcnow()
        summary = {
            'phase': phase.value,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration_seconds': (end_time - start_time).total_seconds(),
            'total_tests': len(all_results),
            'passed': sum(1 for r in all_results if r.result == TestResult.PASS),
            'failed': sum(1 for r in all_results if r.result == TestResult.FAIL),
            'warnings': sum(1 for r in all_results if r.result == TestResult.WARN),
            'skipped': sum(1 for r in all_results if r.result == TestResult.SKIP),
            'results': [asdict(r) for r in all_results]
        }

        # Publish results
        publish_results(summary, self.auth.get_hub_session(), self.s3_bucket)

        return summary

    def discover_and_generate_golden_path(self, accounts: List[AccountConfig], tgw_id: str = None) -> Dict:
        """
        Alias for discover_baseline for backward compatibility.
        """
        return self.discover_baseline(accounts, tgw_id)

    def run_test_suite(self, accounts: List[AccountConfig], phase: TestPhase, parallel: bool = True) -> Dict:
        """
        Alias for run_tests for backward compatibility.
        """
        return self.run_tests(accounts, phase, parallel)
