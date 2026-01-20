aft-network-testing/
├── README.md
├── requirements.txt
├── setup.py
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── auth.py                        # Authentication & session management
│   ├── models.py                      # Data models & enums
│   ├── baseline_discovery.py         # VPC baseline discovery
│   ├── connectivity_discovery.py     # Multi-connection type discovery
│   ├── reachability_tester.py        # Multi-connection reachability testing
│   ├── orchestrator.py                # Main test orchestrator
│   └── cli.py                         # Command-line interface
│
├── config/
│   ├── accounts.yaml.example          # Example account configuration
│   └── golden_path.yaml.example       # Example golden path
│
├── deployment/
│   ├── terraform/
│   │   ├── lambda.tf                  # Lambda deployment
│   │   ├── iam.tf                     # IAM roles and policies
│   │   ├── s3.tf                      # Results bucket
│   │   ├── cloudwatch.tf              # Metrics and dashboards
│   │   └── variables.tf               # Terraform variables
│   │
│   ├── lambda_deployment/
│   │   ├── lambda_function.py         # Lambda handler
│   │   └── requirements.txt           # Lambda dependencies
│   │
│   └── ci-cd/
│       ├── buildspec.yml              # AWS CodeBuild
│       ├── .gitlab-ci.yml             # GitLab CI/CD
│       └── github-actions.yml         # GitHub Actions
│
├── docs/
│   ├── QUICKSTART.md
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   └── API.md
│
└── tests/
    ├── test_discovery.py
    ├── test_reachability.py
    └── test_orchestrator.py