"""
Lambda function to test network connectivity from hub to spoke VPCs
Deploy this in your shared services/hub account
Requires VPC configuration with access to Transit Gateway
"""

import socket
import time
from typing import Dict


def ping_host(ip: str, count: int = 3, timeout: int = 2) -> Dict:
    """Test ICMP connectivity"""
    try:
        # Note: Lambda containers don't support ICMP by default
        # This is a workaround using TCP connection test
        # For true ICMP, deploy EC2-based tester or use VPC Reachability Analyzer

        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        # Try common ports as proxy for reachability
        ports_to_try = [80, 443, 22]
        reachable = False

        for port in ports_to_try:
            try:
                result = sock.connect_ex((ip, port))
                if result == 0:
                    reachable = True
                    break
            except:
                continue

        latency = (time.time() - start) * 1000
        sock.close()

        return {
            'reachable': reachable,
            'latency_ms': round(latency, 2),
            'method': 'tcp_probe'
        }
    except Exception as e:
        return {
            'reachable': False,
            'error': str(e),
            'method': 'tcp_probe'
        }


def test_tcp_connection(ip: str, port: int, timeout: int = 5) -> Dict:
    """Test TCP connectivity to specific port"""
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        result = sock.connect_ex((ip, port))
        latency = (time.time() - start) * 1000
        sock.close()

        return {
            'reachable': result == 0,
            'port': port,
            'latency_ms': round(latency, 2),
            'error_code': result if result != 0 else None
        }
    except Exception as e:
        return {
            'reachable': False,
            'port': port,
            'error': str(e)
        }


def test_dns_resolution(hostname: str) -> Dict:
    """Test DNS resolution"""
    try:
        start = time.time()
        ip_address = socket.gethostbyname(hostname)
        latency = (time.time() - start) * 1000

        return {
            'resolved': True,
            'ip_address': ip_address,
            'latency_ms': round(latency, 2)
        }
    except Exception as e:
        return {
            'resolved': False,
            'error': str(e)
        }


def lambda_handler(event, context):
    """
    Lambda handler for connectivity testing

    Expected event format:
    {
        "target_ip": "10.1.1.100",
        "account_id": "111111111111",
        "test_type": "icmp" | "tcp" | "dns",
        "port": 80  # Required for tcp tests
        "hostname": "example.com"  # Required for dns tests
    }
    """

    target_ip = event.get('target_ip')
    account_id = event.get('account_id')
    test_type = event.get('test_type', 'tcp')
    port = event.get('port', 80)
    hostname = event.get('hostname')

    result = {
        'account_id': account_id,
        'target_ip': target_ip,
        'test_type': test_type,
        'timestamp': int(time.time())
    }

    if test_type == 'icmp':
        result.update(ping_host(target_ip))
    elif test_type == 'tcp':
        result.update(test_tcp_connection(target_ip, port))
    elif test_type == 'dns' and hostname:
        result.update(test_dns_resolution(hostname))
    else:
        result['error'] = f"Unknown test type: {test_type}"
        result['reachable'] = False

    return result