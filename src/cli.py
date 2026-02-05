#!/usr/bin/env python3
"""
AFT Network Testing Framework - CLI Entry Point

Usage:
    python cli.py --mode local --phase discover --accounts-file accounts.yaml
    python cli.py --mode local --phase post-release --accounts-file accounts.yaml --golden-path golden_path.yaml
"""

import argparse
import sys
import yaml

from models import ExecutionMode, TestPhase, AccountConfig
from auth import AuthConfig
from orchestrator import AFTTestOrchestrator
from reporting import print_summary


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='AFT Network Testing Framework',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover all connectivity types (TGW, Peering, VPN, PrivateLink)
  aft-test --mode local --profile my-profile --phase discover --accounts-file accounts.yaml

  # Discover with specific TGW
  aft-test --mode local --profile my-profile --phase discover --accounts-file accounts.yaml --tgw-id tgw-abc123

  # Discover only specific connection types
  aft-test --mode local --profile my-profile --phase discover --accounts-file accounts.yaml --connection-types tgw,peering

  # Export test plan for review/editing (from golden path)
  aft-test --phase export-test-plan --golden-path golden_path.yaml --test-plan test_plan.yaml

  # Run tests from a test plan (after review/editing)
  aft-test --mode local --profile my-profile --phase run-test-plan --test-plan test_plan.yaml

  # Run pre-release tests (before Terraform apply)
  aft-test --mode local --profile my-profile --phase pre-release --accounts-file accounts.yaml --golden-path golden_path.yaml

  # Run post-release tests (after Terraform apply)
  aft-test --mode aws --phase post-release --accounts-file accounts.yaml --golden-path golden_path.yaml --s3-bucket my-results-bucket
        """
    )

    parser.add_argument(
        '--mode',
        choices=['local', 'aws', 'codebuild'],
        default='local',
        help='Execution mode (default: local)'
    )

    parser.add_argument(
        '--profile',
        help='AWS CLI profile name for hub account (local mode only)'
    )

    parser.add_argument(
        '--profile-pattern',
        help='Profile pattern for per-account credentials, e.g., "{account_id}" or "acct-{account_id}"'
    )

    parser.add_argument(
        '--role',
        default='AWSAFTExecution',
        help='IAM role to assume in target accounts (default: AWSAFTExecution)'
    )

    parser.add_argument(
        '--region',
        default='us-west-2',
        help='AWS region (default: us-west-2)'
    )

    parser.add_argument(
        '--phase',
        choices=['discover', 'pre-release', 'post-release', 'export-test-plan', 'run-test-plan'],
        required=True,
        help='Test phase to execute'
    )

    parser.add_argument(
        '--test-plan',
        default='./test_plan.yaml',
        help='Test plan YAML file for export/run (default: ./test_plan.yaml)'
    )

    parser.add_argument(
        '--only-active',
        action='store_true',
        help='Export only patterns with observed traffic (export-test-plan phase)'
    )

    parser.add_argument(
        '--ports',
        help='Generate port-specific tests for these ports, comma-separated (e.g., 443,22). Works without flow logs.'
    )

    parser.add_argument(
        '--test-ports',
        help='Alias for --ports (deprecated)'
    )

    parser.add_argument(
        '--include-protocol-level',
        action='store_true',
        help='Include protocol-level tests (port=null) for production readiness checks'
    )

    parser.add_argument(
        '--accounts-file',
        default='config/accounts.yaml',
        help='YAML file with account configurations (default: config/accounts.yaml)'
    )

    parser.add_argument(
        '--tgw-id',
        help='Transit Gateway ID (optional, for TGW connectivity discovery)'
    )

    parser.add_argument(
        '--connection-types',
        default='all',
        help='Connection types to discover: all, or comma-separated list of tgw,peering,vpn,privatelink (default: all)'
    )

    parser.add_argument(
        '--golden-path',
        default='./golden_path.yaml',
        help='Golden path YAML file (default: ./golden_path.yaml)'
    )

    parser.add_argument(
        '--s3-bucket',
        help='S3 bucket for results storage'
    )

    parser.add_argument(
        '--publish-results',
        action='store_true',
        default=False,
        help='Publish results to CloudWatch and S3 (default: False)'
    )

    parser.add_argument(
        '--parallel',
        type=int,
        default=3,
        help='Number of parallel tests (default: 3, use 1 for sequential)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate configuration without executing'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )

    return parser.parse_args()


def load_accounts(accounts_file: str) -> list:
    """Load account configurations from YAML file."""
    with open(accounts_file, 'r') as f:
        accounts_data = yaml.safe_load(f)

    if not accounts_data:
        return []

    accounts = []
    for acc in accounts_data.get('accounts', []):
        accounts.append(AccountConfig(**acc))

    return accounts


def get_execution_mode(mode_str: str) -> ExecutionMode:
    """Convert mode string to ExecutionMode enum."""
    mode_map = {
        'local': ExecutionMode.LOCAL,
        'aws': ExecutionMode.AWS_LAMBDA,
        'codebuild': ExecutionMode.AWS_CODEBUILD
    }
    return mode_map.get(mode_str, ExecutionMode.LOCAL)


def main():
    """Main entry point."""
    args = parse_args()

    # Setup authentication
    exec_mode = get_execution_mode(args.mode)

    # Validate local mode has required auth
    if exec_mode == ExecutionMode.LOCAL and args.phase not in ['export-test-plan']:
        if not args.profile and not args.profile_pattern:
            print("Error: Local mode requires either --profile or --profile-pattern")
            sys.exit(1)

    auth = AuthConfig(
        mode=exec_mode,
        profile_name=args.profile,
        profile_pattern=args.profile_pattern,
        role_name=args.role,
        region=args.region
    )

    # Load account configurations (not needed for test-plan phases)
    accounts = []
    if args.phase not in ['export-test-plan', 'run-test-plan']:
        try:
            accounts = load_accounts(args.accounts_file)
        except FileNotFoundError:
            print(f"Error: Accounts file not found: {args.accounts_file}")
            sys.exit(1)
        except Exception as e:
            print(f"Error loading accounts file: {str(e)}")
            sys.exit(1)

    if args.verbose:
        print(f"Loaded {len(accounts)} accounts from {args.accounts_file}")

    # Parse connection types
    if args.connection_types == 'all':
        connection_types = ['tgw', 'peering', 'vpn', 'privatelink']
    else:
        connection_types = [t.strip() for t in args.connection_types.split(',')]

    # Dry run - validate configuration only
    if args.dry_run:
        print("Dry run mode - validating configuration...")
        print(f"  Mode: {args.mode}")
        print(f"  Profile: {args.profile or 'default'}")
        print(f"  Region: {args.region}")
        print(f"  Phase: {args.phase}")
        print(f"  Accounts: {len(accounts)}")
        print(f"  Golden Path: {args.golden_path}")
        print(f"  TGW ID: {args.tgw_id or 'auto-discover'}")
        print(f"  Connection Types: {', '.join(connection_types)}")
        print(f"  S3 Bucket: {args.s3_bucket or 'not specified'}")
        print("\nConfiguration valid. Ready to execute.")
        sys.exit(0)

    # Initialize orchestrator
    orchestrator = AFTTestOrchestrator(
        auth_config=auth,
        golden_path_file=args.golden_path,
        s3_bucket=args.s3_bucket
    )

    # Execute based on phase
    if args.phase == 'discover':
        # Discovery phase
        golden_path = orchestrator.discover_baseline(
            accounts,
            tgw_id=args.tgw_id,
            connection_types=connection_types
        )

        print(f"\n✓ Discovery complete. Found {len(accounts)} accounts.")

        if 'connectivity' in golden_path:
            conn = golden_path['connectivity']
            print(f"✓ Discovered {conn['total_paths']} connectivity paths")
            print(f"✓ Observed actual traffic on {conn.get('active_paths', 0)} paths")

            # Print breakdown by connection type
            by_type = conn.get('by_connection_type', {})
            if by_type:
                print("\nBy connection type:")
                for conn_type, count in by_type.items():
                    if count > 0:
                        print(f"  {conn_type.upper()}: {count}")

        sys.exit(0)

    elif args.phase == 'export-test-plan':
        # Parse ports filter (filters observed ports)
        ports_filter = None
        if args.ports:
            ports_filter = [int(p.strip()) for p in args.ports.split(',')]

        # Parse test_ports (generates tests for these ports on all patterns)
        test_ports_list = None
        if args.test_ports:
            test_ports_list = [int(p.strip()) for p in args.test_ports.split(',')]

        # Parse connection types (reuse from discover phase)
        conn_types_filter = None
        if args.connection_types != 'all':
            conn_types_filter = [t.strip() for t in args.connection_types.split(',')]

        # Export test plan for review
        result = orchestrator.export_test_plan(
            args.test_plan,
            only_active=args.only_active,
            ports=ports_filter,
            connection_types=conn_types_filter,
            test_ports=test_ports_list,
            include_protocol_level=args.include_protocol_level
        )
        print(f"\n✓ Exported {result['tests_exported']} tests to {result['output_file']}")
        if result.get('filtered_patterns'):
            print(f"  ({result['filtered_patterns']} patterns filtered out)")
        if result.get('filtered_ports'):
            print(f"  ({result['filtered_ports']} port tests filtered out)")
        print("\nYou can now:")
        print(f"  1. Review/edit {args.test_plan}")
        print(f"  2. Set 'enabled: false' on tests to skip")
        print(f"  3. Add notes for documentation")
        print(f"  4. Run: aft-test --phase run-test-plan --test-plan {args.test_plan}")
        sys.exit(0)

    elif args.phase == 'run-test-plan':
        # Run from test plan file
        try:
            summary = orchestrator.run_from_test_plan(
                args.test_plan,
                args.publish_results,
                max_parallel=args.parallel
            )
        except FileNotFoundError:
            print(f"Error: Test plan not found: {args.test_plan}")
            print(f"Run 'aft-test --phase export-test-plan' first to generate a test plan.")
            sys.exit(1)

        print_summary(summary)
        sys.exit(0 if summary['failed'] == 0 else 1)

    else:
        # Test execution phases
        phase_map = {
            'pre-release': TestPhase.PRE_RELEASE,
            'post-release': TestPhase.POST_RELEASE
        }
        phase = phase_map.get(args.phase)

        if not phase:
            print(f"Error: Unknown phase: {args.phase}")
            sys.exit(1)

        summary = orchestrator.run_tests(accounts, phase, args.parallel, args.publish_results)

        # Print summary
        print_summary(summary)

        # Exit code for CI/CD
        sys.exit(0 if summary['failed'] == 0 else 1)


if __name__ == "__main__":
    main()
