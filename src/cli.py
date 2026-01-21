#!/usr/bin/env python3
"""
AFT Network Testing Framework - CLI Entry Point

Usage:
    python cli.py --mode local --phase discover --accounts-file accounts.yaml
    python cli.py --mode local --phase pre-flight --accounts-file accounts.yaml --golden-path golden_path.yaml
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
  # Discover baseline and generate golden path
  python cli.py --mode local --profile my-profile --phase discover --accounts-file accounts.yaml --tgw-id tgw-abc123

  # Run pre-flight tests
  python cli.py --mode local --profile my-profile --phase pre-flight --accounts-file accounts.yaml --golden-path golden_path.yaml

  # Run post-release tests with S3 storage
  python cli.py --mode aws --phase post-release --accounts-file accounts.yaml --golden-path golden_path.yaml --s3-bucket my-results-bucket
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
        help='AWS region (default: us-east-1)'
    )

    parser.add_argument(
        '--phase',
        choices=['discover', 'pre-release', 'pre-flight', 'post-release'],
        required=True,
        help='Test phase to execute'
    )

    parser.add_argument(
        '--accounts-file',
        default='config/accounts.yaml',
        help='YAML file with account configurations (default: config/accounts.yaml)'
    )

    parser.add_argument(
        '--tgw-id',
        help='Transit Gateway ID for connectivity discovery'
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
        '--parallel',
        action='store_true',
        default=True,
        help='Run tests in parallel (default: True)'
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
    auth = AuthConfig(
        mode=exec_mode,
        profile_name=args.profile,
        profile_pattern=args.profile_pattern,
        role_name=args.role,
        region=args.region
    )

    # Load account configurations
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

    # Dry run - validate configuration only
    if args.dry_run:
        print("Dry run mode - validating configuration...")
        print(f"  Mode: {args.mode}")
        print(f"  Profile: {args.profile or 'default'}")
        print(f"  Region: {args.region}")
        print(f"  Phase: {args.phase}")
        print(f"  Accounts: {len(accounts)}")
        print(f"  Golden Path: {args.golden_path}")
        print(f"  TGW ID: {args.tgw_id or 'not specified'}")
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
        golden_path = orchestrator.discover_baseline(accounts, args.tgw_id)

        print(f"\n✓ Discovery complete. Found {len(accounts)} accounts.")

        if 'connectivity' in golden_path:
            conn = golden_path['connectivity']
            print(f"✓ Discovered {conn['total_paths']} VPC-to-VPC connectivity paths")
            print(f"✓ Observed actual traffic on {conn.get('active_paths', 0)} paths")

        sys.exit(0)

    else:
        # Test execution phases
        phase_map = {
            'pre-release': TestPhase.PRE_RELEASE,
            'pre-flight': TestPhase.PRE_FLIGHT,
            'post-release': TestPhase.POST_RELEASE
        }
        phase = phase_map.get(args.phase)

        if not phase:
            print(f"Error: Unknown phase: {args.phase}")
            sys.exit(1)

        summary = orchestrator.run_tests(accounts, phase, args.parallel)

        # Print summary
        print_summary(summary)

        # Exit code for CI/CD
        sys.exit(0 if summary['failed'] == 0 else 1)


if __name__ == "__main__":
    main()
