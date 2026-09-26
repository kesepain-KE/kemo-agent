"""Compatibility entry point for the formal deployment suite.

Canonical assertions live in ``tests/deploy/test_deploy.py`` so the repository's
normal ``pytest tests`` release gate cannot accidentally omit this subsystem.
"""
from tests.deploy.test_deploy import DeploymentTests
