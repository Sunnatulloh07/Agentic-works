"""Environment for the dependency-backed HTTP suite.

Seven of these modules set ``ENV`` for themselves and one did not, which made the
whole directory uncollectable for a reason that had nothing to do with any test:
``test_connector_authority_http`` imports ``app.platform_api`` on its fourth line
and only reaches ``test_control_plane_hardening_http`` -- the module that would
have set the variable -- on its sixth, and pytest collects it first
alphabetically.  ``app.platform_api`` imports ``app.auth``, which validates the
runtime config at import time and fails closed, so collection died with
``ConfigError`` before a single test ran.

Setting it here removes the ordering dependency, because pytest imports
``conftest`` before any test module in the directory.  The per-module
``setdefault`` calls are left in place: they are harmless, and removing them would
make these files unrunnable one at a time.
"""
import os

os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')
