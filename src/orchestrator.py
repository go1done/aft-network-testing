"""
AFT Test Orchestrator
Thin orchestration layer that coordinates discovery, testing, and reporting.
"""

import os
import shutil
import yaml
from typing import Dict, List
from dataclasses import asdict
from datetime import datetime


def backup_file_if_exists(filepath: str) -> str:
    """
    Backup existing file with timestamp before overwriting.

    Args:
        filepath: Path to the file to backup

    Returns:
        Path to backup file if created, empty string otherwise
    """
    if not os.path.exists(filepath):
        return ""

    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(filepath)
    backup_path = f"{base}_{timestamp}{ext}"

    # Copy to backup (preserves original until new write succeeds)
    shutil.copy2(filepath, backup_path)
    return backup_path

from models import (
    ExecutionMode,
    TestPhase,
    TestResult,
    AccountConfig,
    ConnectionType,
)
from auth import AuthConfig

from reporting import publish_results, print_summary
from baseline import BaselineDiscovery
from connectivity import ConnectivityDiscovery
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

    def discover_baseline(self,
                          accounts: List[AccountConfig],
                          tgw_id: str = None,
                          connection_types: List[str] = None) -> Dict:
        """
        Phase 0: Discover baseline and generate golden path.

        Args:
            accounts: List of AccountConfig instances
            tgw_id: Optional Transit Gateway ID (required if 'tgw' in connection_types)
            connection_types: List of connection types to discover: 'tgw', 'peering', 'vpn', 'privatelink'
                            Defaults to all types.

        Returns:
            Generated golden path dictionary
        """
        if connection_types is None:
            connection_types = ['tgw', 'peering', 'vpn', 'privatelink']

        print("=" * 80)
        print("PHASE 0: BASELINE DISCOVERY & GOLDEN PATH GENERATION")
        print("=" * 80)
        print(f"Connection types to discover: {', '.join(connection_types)}")

        # Discover VPC configurations
        baselines = self.discovery.scan_all_accounts(accounts)
        golden_path = self.discovery.generate_golden_path(baselines)

        # Build lookup of discovered VPCs from baselines
        discovered_vpcs = {
            b['account_id']: b['vpc']['vpc_id']
            for b in baselines if b and 'vpc' in b
        }

        # Convert AccountConfig to dict for connectivity discovery
        # Use discovered vpc_id from baselines if not provided in AccountConfig
        accounts_dict = [
            {
                'account_id': acc.account_id,
                'account_name': acc.account_name,
                'vpc_id': acc.vpc_id or discovered_vpcs.get(acc.account_id)
            }
            for acc in accounts
        ]

        # Get hub session - use first account as fallback when using profile-pattern
        first_account_id = accounts[0].account_id if accounts else None
        hub_session = self.auth.get_hub_session(fallback_account_id=first_account_id)
        hub_account_id = hub_session.client('sts').get_caller_identity()['Account']

        conn_discovery = ConnectivityDiscovery(
            auth_config=self.auth,
            hub_account_id=hub_account_id,
            fallback_account_id=first_account_id
        )

        # Determine which connection types to discover
        discover_tgw = 'tgw' in connection_types
        discover_peering = 'peering' in connection_types
        discover_vpn = 'vpn' in connection_types
        discover_privatelink = 'privatelink' in connection_types

        connectivity_patterns = conn_discovery.build_connectivity_map(
            accounts_dict,
            tgw_id=tgw_id,  # If None, TGWs will be auto-discovered from account attachments
            discover_tgw=discover_tgw,
            discover_peering=discover_peering,
            discover_vpn=discover_vpn,
            discover_privatelink=discover_privatelink,
            use_flow_logs=True,
            baselines=baselines  # Pass baselines for security group port extraction
        )

        # Build connectivity section with all connection types
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
                    'expected_reachable': p.expected,
                    'traffic_observed': p.traffic_observed,
                    'protocols_observed': list(p.protocols_observed),
                    'ports_observed': sorted(list(p.ports_observed)),
                    'ports_allowed': sorted(list(p.ports_allowed)),
                    'use_case': p.use_case
                }
                for p in connectivity_patterns
            ],
            'tgw_id': tgw_id,
            'total_paths': len(connectivity_patterns),
            'active_paths': sum(1 for p in connectivity_patterns if p.traffic_observed),
            'by_connection_type': {
                'tgw': sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.TRANSIT_GATEWAY),
                'peering': sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.VPC_PEERING),
                'vpn': sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.VPN),
                'privatelink': sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.PRIVATELINK),
            }
        }

        # Save golden path (backup existing file first)
        backup_path = backup_file_if_exists(self.golden_path_file)
        if backup_path:
            print(f"  ℹ️  Previous golden path backed up to {backup_path}")

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

    def run_tests(self, accounts: List[AccountConfig], phase: TestPhase, parallel: bool = True, publish: bool = False) -> Dict:
        """
        Execute comprehensive test suite for all connection types.

        Args:
            accounts: List of AccountConfig instances
            phase: Test phase (PRE_RELEASE, PRE_FLIGHT, POST_RELEASE)
            parallel: Whether to run tests in parallel (reserved for future use)
            publish: Whether to publish results to CloudWatch/S3 (default: False)

        Returns:
            Test summary dictionary
        """
        print(f"\n{'=' * 80}")
        print(f"PHASE: {phase.value.upper()}")
        print(f"{'=' * 80}")

        # Set fallback account for profile-pattern mode
        if accounts:
            self.tester.set_fallback_account(accounts[0].account_id)

        start_time = datetime.utcnow()
        all_results = []

        # Load connectivity patterns from golden path
        connectivity_tests = []
        if self.golden_path and 'connectivity' in self.golden_path:
            patterns = self.golden_path['connectivity'].get('patterns', [])

            for pattern in patterns:
                if not pattern.get('expected_reachable'):
                    continue

                # Get connection type and ID
                conn_type_str = pattern.get('connection_type', 'tgw')
                connection_id = pattern.get('connection_id')

                # Protocol-level test
                connectivity_tests.append({
                    'source_vpc': pattern['source_vpc_id'],
                    'source_account': pattern['source_account_name'],
                    'dest_vpc': pattern['dest_vpc_id'],
                    'dest_account': pattern['dest_account_name'],
                    'connection_type': conn_type_str,
                    'connection_id': connection_id,
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
                            'connection_type': conn_type_str,
                            'connection_id': connection_id,
                            'protocol': 'tcp',
                            'port': port
                        })

        # Count tests by connection type
        by_type = {}
        for test in connectivity_tests:
            conn_type = test['connection_type']
            by_type[conn_type] = by_type.get(conn_type, 0) + 1

        print(f"Generated {len(connectivity_tests)} connectivity tests from golden path")
        for conn_type, count in by_type.items():
            print(f"  {conn_type.upper()}: {count} tests")

        # Execute connectivity tests
        if phase != TestPhase.PRE_RELEASE:
            for test in connectivity_tests:
                conn_type_str = test['connection_type']
                print(
                    f"\nTesting [{conn_type_str.upper()}]: {test['source_account']} → {test['dest_account']} "
                    f"({test['protocol']}:{test.get('port', 'all')})"
                )

                # Map string to ConnectionType enum
                conn_type_map = {
                    'tgw': ConnectionType.TRANSIT_GATEWAY,
                    'pcx': ConnectionType.VPC_PEERING,
                    'vpn': ConnectionType.VPN,
                    'vpce': ConnectionType.PRIVATELINK,
                }
                connection_type = conn_type_map.get(conn_type_str, ConnectionType.TRANSIT_GATEWAY)

                # Use unified test_connectivity method that dispatches by connection type
                result = self.tester.test_connectivity(
                    connection_type=connection_type,
                    source_vpc=test['source_vpc'],
                    dest_vpc=test['dest_vpc'],
                    connection_id=test['connection_id'],
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

        # Publish results if enabled
        if publish:
            first_account_id = accounts[0].account_id if accounts else None
            publish_results(summary, self.auth.get_hub_session(fallback_account_id=first_account_id), self.s3_bucket)

        return summary

    def discover_and_generate_golden_path(self,
                                          accounts: List[AccountConfig],
                                          tgw_id: str = None,
                                          connection_types: List[str] = None) -> Dict:
        """
        Alias for discover_baseline for backward compatibility.
        """
        return self.discover_baseline(accounts, tgw_id, connection_types)

    def run_test_suite(self, accounts: List[AccountConfig], phase: TestPhase, parallel: bool = True, publish: bool = False) -> Dict:
        """
        Alias for run_tests for backward compatibility.
        """
        return self.run_tests(accounts, phase, parallel, publish)

    def export_test_plan(self,
                         output_file: str,
                         only_active: bool = False,
                         ports: List[int] = None,
                         connection_types: List[str] = None,
                         test_ports: List[int] = None,
                         include_protocol_level: bool = False) -> Dict:
        """
        Export test cases to a reviewable/editable YAML file.

        Generates a test plan from the golden path that can be:
        - Reviewed before execution
        - Modified (enable/disable tests, add notes)
        - Loaded back for execution via run_from_test_plan()

        Args:
            output_file: Path to write the test plan YAML
            only_active: Only include patterns with traffic_observed=True
            ports: Filter patterns to those with these ports allowed by security groups,
                  and generate port-specific tests for them (e.g., [443, 22]).
                  When not specified, uses all ports_allowed from the golden path.
            connection_types: Only include these connection types (e.g., ['tgw', 'peering'])
                            Accepts both user-friendly names (peering, privatelink) and
                            enum values (pcx, vpce)
            test_ports: Generate tests for these ports regardless of ports_allowed (deprecated)
            include_protocol_level: Include protocol-level tests (port=null) for
                                   production readiness checks. Default: False.

        Returns:
            Summary dict with tests_exported count and filters applied

        Raises:
            ValueError: If no golden path is loaded
        """
        if not self.golden_path:
            raise ValueError("No golden path loaded. Run discover_baseline first.")

        # Map user-friendly connection type names to enum values used in golden path
        conn_type_aliases = {
            'peering': 'pcx',
            'privatelink': 'vpce',
        }

        # Normalize connection_types filter to use enum values
        normalized_conn_types = None
        if connection_types:
            normalized_conn_types = [
                conn_type_aliases.get(ct, ct) for ct in connection_types
            ]

        tests = []
        test_id = 1
        filtered_patterns = 0

        # Load connectivity patterns from golden path
        if 'connectivity' in self.golden_path:
            patterns = self.golden_path['connectivity'].get('patterns', [])

            for pattern in patterns:
                if not pattern.get('expected_reachable'):
                    continue

                # Filter: only_active
                if only_active and not pattern.get('traffic_observed'):
                    filtered_patterns += 1
                    continue

                conn_type = pattern.get('connection_type', 'tgw')

                # Filter: connection_types (using normalized values)
                if normalized_conn_types and conn_type not in normalized_conn_types:
                    filtered_patterns += 1
                    continue

                # Get allowed ports from security groups (discovered during baseline)
                pattern_ports_allowed = set(pattern.get('ports_allowed', []))

                # Filter: ports - only include patterns that allow the specified ports
                if ports:
                    matching_ports = pattern_ports_allowed & set(ports)
                    if not matching_ports:
                        filtered_patterns += 1
                        continue

                connection_id = pattern.get('connection_id')

                # Protocol-level test (optional - for production readiness checks)
                if include_protocol_level:
                    tests.append({
                        'id': f'test-{test_id:03d}',
                        'enabled': True,
                        'source_vpc': pattern['source_vpc_id'],
                        'source_account': pattern['source_account_name'],
                        'dest_vpc': pattern['dest_vpc_id'],
                        'dest_account': pattern['dest_account_name'],
                        'connection_type': conn_type,
                        'connection_id': connection_id,
                        'protocol': '-1',
                        'port': None,
                        'description': f"Protocol-level: {pattern['source_account_name']} -> {pattern['dest_account_name']}",
                        'notes': 'Production readiness check',
                    })
                    test_id += 1

                # Port-specific tests
                # Determine which ports to test for this pattern
                ports_to_test = set()

                if ports:
                    # Use intersection of requested ports and allowed ports
                    ports_to_test = pattern_ports_allowed & set(ports)
                elif test_ports:
                    # test_ports bypasses allowed check (deprecated)
                    ports_to_test.update(test_ports)
                elif pattern_ports_allowed:
                    # Use all allowed ports from security groups
                    ports_to_test = pattern_ports_allowed
                elif pattern.get('traffic_observed'):
                    # Fall back to observed ports if no allowed ports discovered
                    ports_to_test.update(pattern.get('ports_observed', []))

                # Generate tests for collected ports
                for port in sorted(ports_to_test):
                    tests.append({
                        'id': f'test-{test_id:03d}',
                        'enabled': True,
                        'source_vpc': pattern['source_vpc_id'],
                        'source_account': pattern['source_account_name'],
                        'dest_vpc': pattern['dest_vpc_id'],
                        'dest_account': pattern['dest_account_name'],
                        'connection_type': conn_type,
                        'connection_id': connection_id,
                        'protocol': 'tcp',
                        'port': port,
                        'description': f"TCP:{port} {pattern['source_account_name']} -> {pattern['dest_account_name']}",
                        'notes': '',
                    })
                    test_id += 1

        # Build filters summary for metadata
        filters_applied = {}
        if only_active:
            filters_applied['only_active'] = True
        if ports:
            filters_applied['ports'] = ports
        if connection_types:
            filters_applied['connection_types'] = connection_types
        if test_ports:
            filters_applied['test_ports'] = test_ports
        if include_protocol_level:
            filters_applied['include_protocol_level'] = True

        test_plan = {
            'version': '1.0',
            'generated_at': datetime.utcnow().isoformat(),
            'source_golden_path': self.golden_path_file,
            'filters': filters_applied if filters_applied else None,
            'tests': tests,
        }

        # Backup existing test plan first
        backup_path = backup_file_if_exists(output_file)
        if backup_path:
            print(f"  ℹ️  Previous test plan backed up to {backup_path}")

        with open(output_file, 'w') as f:
            yaml.dump(test_plan, f, default_flow_style=False, sort_keys=False)

        print(f"Exported {len(tests)} tests to {output_file}")
        if filtered_patterns:
            print(f"  Filtered out {filtered_patterns} patterns")

        return {
            'tests_exported': len(tests),
            'output_file': output_file,
            'filtered_patterns': filtered_patterns,
        }

    def run_from_test_plan(self, test_plan_file: str, publish: bool = False) -> Dict:
        """
        Execute tests from a test plan file.

        Loads a test plan YAML (possibly modified by user) and runs
        only the enabled tests.

        Args:
            test_plan_file: Path to test plan YAML file
            publish: Whether to publish results to CloudWatch/S3

        Returns:
            Test summary dictionary

        Raises:
            FileNotFoundError: If test plan file doesn't exist
        """
        if not os.path.exists(test_plan_file):
            raise FileNotFoundError(f"Test plan not found: {test_plan_file}")

        with open(test_plan_file, 'r') as f:
            test_plan = yaml.safe_load(f)

        print(f"\n{'=' * 80}")
        print("EXECUTING TEST PLAN")
        print(f"{'=' * 80}")
        print(f"Source: {test_plan_file}")

        tests = test_plan.get('tests', [])
        enabled_tests = [t for t in tests if t.get('enabled', True)]
        disabled_tests = [t for t in tests if not t.get('enabled', True)]

        print(f"Total tests: {len(tests)}")
        print(f"Enabled: {len(enabled_tests)}, Disabled: {len(disabled_tests)}")

        start_time = datetime.utcnow()
        all_results = []

        # Execute enabled tests
        for test in enabled_tests:
            print(
                f"\nTesting [{test['connection_type'].upper()}]: "
                f"{test['source_account']} -> {test['dest_account']} "
                f"({test['protocol']}:{test.get('port', 'all')})"
            )

            # Map string to ConnectionType enum
            conn_type_map = {
                'tgw': ConnectionType.TRANSIT_GATEWAY,
                'pcx': ConnectionType.VPC_PEERING,
                'vpn': ConnectionType.VPN,
                'vpce': ConnectionType.PRIVATELINK,
            }
            connection_type = conn_type_map.get(
                test['connection_type'],
                ConnectionType.TRANSIT_GATEWAY
            )

            result = self.tester.test_connectivity(
                connection_type=connection_type,
                source_vpc=test['source_vpc'],
                dest_vpc=test['dest_vpc'],
                connection_id=test['connection_id'],
                protocol=test['protocol'],
                port=test.get('port')
            )

            all_results.append(result)

            status_icon = "✓" if result.result == TestResult.PASS else "✗"
            print(f"  {status_icon} {result.name}: {result.message}")

        end_time = datetime.utcnow()
        summary = {
            'phase': 'test-plan',
            'source_file': test_plan_file,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration_seconds': (end_time - start_time).total_seconds(),
            'total_tests': len(all_results),
            'passed': sum(1 for r in all_results if r.result == TestResult.PASS),
            'failed': sum(1 for r in all_results if r.result == TestResult.FAIL),
            'warnings': sum(1 for r in all_results if r.result == TestResult.WARN),
            'skipped': len(disabled_tests),
            'results': [asdict(r) for r in all_results]
        }

        if publish and self.auth:
            publish_results(summary, self.auth.get_hub_session(), self.s3_bucket)

        return summary
