"""
Reporting Module
Handles CloudWatch metrics publishing and S3 results storage.
"""

import json
from typing import Dict


def publish_results(summary: Dict, hub_session, s3_bucket: str = None) -> bool:
    """
    Publish test results to CloudWatch Metrics and S3.

    Args:
        summary: Test summary dictionary containing:
            - phase: Test phase name
            - passed: Number of passed tests
            - failed: Number of failed tests
            - duration_seconds: Total test duration
            - start_time: ISO formatted start time
        hub_session: boto3.Session for hub account
        s3_bucket: Optional S3 bucket name for results storage

    Returns:
        True if all tests passed (no failures), False otherwise
    """
    # CloudWatch Metrics
    try:
        cloudwatch = hub_session.client('cloudwatch')
        cloudwatch.put_metric_data(
            Namespace='AFT/VPCTests',
            MetricData=[
                {
                    'MetricName': 'TestsPassed',
                    'Value': summary['passed'],
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary['phase']}]
                },
                {
                    'MetricName': 'TestsFailed',
                    'Value': summary['failed'],
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary['phase']}]
                },
                {
                    'MetricName': 'TestDuration',
                    'Value': summary['duration_seconds'],
                    'Unit': 'Seconds',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary['phase']}]
                }
            ]
        )
        print("\n✓ Published metrics to CloudWatch")
    except Exception as e:
        print(f"\n✗ Failed to publish CloudWatch metrics: {str(e)}")

    # S3 Results
    if s3_bucket:
        try:
            s3 = hub_session.client('s3')
            key = f"vpc-tests/{summary['phase']}/{summary['start_time']}.json"

            s3.put_object(
                Bucket=s3_bucket,
                Key=key,
                Body=json.dumps(summary, indent=2),
                ContentType='application/json'
            )
            print(f"✓ Results saved to s3://{s3_bucket}/{key}")
        except Exception as e:
            print(f"✗ Failed to save to S3: {str(e)}")

    return summary['failed'] == 0


def publish_cloudwatch_metrics(summary: Dict, hub_session) -> bool:
    """
    Publish test metrics to CloudWatch.

    Args:
        summary: Test summary with phase, passed, failed, duration_seconds
        hub_session: boto3.Session for hub account

    Returns:
        True if successful, False otherwise
    """
    try:
        cloudwatch = hub_session.client('cloudwatch')
        cloudwatch.put_metric_data(
            Namespace='AFT/VPCTests',
            MetricData=[
                {
                    'MetricName': 'TestsPassed',
                    'Value': summary.get('passed', 0),
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                },
                {
                    'MetricName': 'TestsFailed',
                    'Value': summary.get('failed', 0),
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                },
                {
                    'MetricName': 'TestsWarnings',
                    'Value': summary.get('warnings', 0),
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                },
                {
                    'MetricName': 'TestsSkipped',
                    'Value': summary.get('skipped', 0),
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                },
                {
                    'MetricName': 'TestDuration',
                    'Value': summary.get('duration_seconds', 0),
                    'Unit': 'Seconds',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                },
                {
                    'MetricName': 'TotalTests',
                    'Value': summary.get('total_tests', 0),
                    'Unit': 'Count',
                    'Dimensions': [{'Name': 'Phase', 'Value': summary.get('phase', 'unknown')}]
                }
            ]
        )
        return True
    except Exception as e:
        print(f"CloudWatch publish error: {str(e)}")
        return False


def save_to_s3(summary: Dict, hub_session, s3_bucket: str) -> bool:
    """
    Save test results to S3.

    Args:
        summary: Test summary dictionary
        hub_session: boto3.Session for hub account
        s3_bucket: S3 bucket name

    Returns:
        True if successful, False otherwise
    """
    try:
        s3 = hub_session.client('s3')
        key = f"vpc-tests/{summary.get('phase', 'unknown')}/{summary.get('start_time', 'unknown')}.json"

        s3.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(summary, indent=2, default=str),
            ContentType='application/json'
        )
        return True
    except Exception as e:
        print(f"S3 save error: {str(e)}")
        return False


def print_summary(summary: Dict):
    """Print a formatted test summary to console."""
    print(f"\n{'=' * 80}")
    print("TEST SUMMARY")
    print(f"{'=' * 80}")
    print(f"Phase: {summary.get('phase', 'unknown')}")
    print(f"Total: {summary.get('total_tests', 0)}")
    print(f"Passed: {summary.get('passed', 0)}")
    print(f"Failed: {summary.get('failed', 0)}")
    print(f"Warnings: {summary.get('warnings', 0)}")
    print(f"Skipped: {summary.get('skipped', 0)}")
    print(f"Duration: {summary.get('duration_seconds', 0):.2f}s")
    print(f"{'=' * 80}")
