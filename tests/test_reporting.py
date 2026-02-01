"""
Tests for reporting module.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from io import StringIO

from reporting import (
    publish_results,
    publish_cloudwatch_metrics,
    save_to_s3,
    print_summary,
)


class TestPublishResults:
    """Test the publish_results function."""

    def test_publish_results_success(self, sample_test_summary):
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.return_value = mock_cloudwatch

        result = publish_results(sample_test_summary, mock_session)

        mock_session.client.assert_called_with('cloudwatch')
        mock_cloudwatch.put_metric_data.assert_called_once()
        # Returns True when no failures
        assert result is False  # sample has 1 failed

    def test_publish_results_no_failures(self):
        summary = {
            "phase": "post-release",
            "passed": 10,
            "failed": 0,
            "duration_seconds": 100,
            "start_time": "2024-01-01T00:00:00",
        }
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.return_value = mock_cloudwatch

        result = publish_results(summary, mock_session)

        assert result is True  # No failures

    def test_publish_results_with_s3(self):
        summary = {
            "phase": "post-release",
            "passed": 10,
            "failed": 0,
            "duration_seconds": 100,
            "start_time": "2024-01-01T00:00:00",
        }
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_s3 = MagicMock()
        mock_session.client.side_effect = lambda svc: mock_cloudwatch if svc == 'cloudwatch' else mock_s3

        result = publish_results(summary, mock_session, s3_bucket="test-bucket")

        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Bucket'] == 'test-bucket'
        assert 'vpc-tests/post-release/' in call_kwargs['Key']

    def test_publish_results_cloudwatch_error(self, sample_test_summary, capsys):
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_cloudwatch.put_metric_data.side_effect = Exception("CloudWatch error")
        mock_session.client.return_value = mock_cloudwatch

        result = publish_results(sample_test_summary, mock_session)

        captured = capsys.readouterr()
        assert "Failed to publish CloudWatch metrics" in captured.out


class TestPublishCloudwatchMetrics:
    """Test CloudWatch metrics publishing."""

    def test_publish_cloudwatch_metrics_success(self):
        summary = {
            "phase": "post-release",
            "passed": 8,
            "failed": 1,
            "warnings": 1,
            "skipped": 0,
            "duration_seconds": 300,
            "total_tests": 10,
        }
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.return_value = mock_cloudwatch

        result = publish_cloudwatch_metrics(summary, mock_session)

        assert result is True
        mock_cloudwatch.put_metric_data.assert_called_once()
        call_args = mock_cloudwatch.put_metric_data.call_args
        assert call_args[1]['Namespace'] == 'AFT/VPCTests'
        # Check metric data contains expected metrics
        metric_names = [m['MetricName'] for m in call_args[1]['MetricData']]
        assert 'TestsPassed' in metric_names
        assert 'TestsFailed' in metric_names
        assert 'TestDuration' in metric_names
        assert 'TotalTests' in metric_names

    def test_publish_cloudwatch_metrics_error(self):
        summary = {"phase": "test", "passed": 0, "failed": 0}
        mock_session = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_cloudwatch.put_metric_data.side_effect = Exception("API Error")
        mock_session.client.return_value = mock_cloudwatch

        result = publish_cloudwatch_metrics(summary, mock_session)

        assert result is False


class TestSaveToS3:
    """Test S3 results saving."""

    def test_save_to_s3_success(self):
        summary = {
            "phase": "post-release",
            "start_time": "2024-01-01T10:00:00",
            "results": [],
        }
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_session.client.return_value = mock_s3

        result = save_to_s3(summary, mock_session, "test-bucket")

        assert result is True
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Bucket'] == 'test-bucket'
        assert call_kwargs['ContentType'] == 'application/json'

    def test_save_to_s3_error(self):
        summary = {"phase": "test", "start_time": "2024-01-01"}
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 Error")
        mock_session.client.return_value = mock_s3

        result = save_to_s3(summary, mock_session, "test-bucket")

        assert result is False


class TestPrintSummary:
    """Test summary printing."""

    def test_print_summary(self, sample_test_summary, capsys):
        print_summary(sample_test_summary)

        captured = capsys.readouterr()
        assert "TEST SUMMARY" in captured.out
        assert "Phase: post-release" in captured.out
        assert "Total: 10" in captured.out
        assert "Passed: 8" in captured.out
        assert "Failed: 1" in captured.out
        assert "Warnings: 1" in captured.out

    def test_print_summary_with_defaults(self, capsys):
        summary = {}  # Empty summary
        print_summary(summary)

        captured = capsys.readouterr()
        assert "TEST SUMMARY" in captured.out
        assert "Phase: unknown" in captured.out
        assert "Total: 0" in captured.out
