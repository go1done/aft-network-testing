from setuptools import setup, find_packages

setup(
    name="aft-network-testing",
    version="1.0.0",
    description="AFT Network Testing Framework with multi-connection type support",
    author="ECP SRE",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "boto3>=1.28.0",
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "aft-test=cli:main",
        ],
    },
    python_requires=">=3.11",
)