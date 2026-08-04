"""
policies/policy_Jorden_Tom_Arik.py
-----------------------------------
Submission entry point. The policy implementation lives in
policies/my_policy.py (the Deterministic Dynamic Priority Index policy);
this file simply re-exports it so the challenge runner keeps working.

If the challenge requires a single self-contained submission file, paste the
full contents of policies/my_policy.py in place of the import below.

**This script should not generate any artifacts or print anything on submission.**
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from policies.my_policy import Submission, GROUP_INFO  # noqa: F401
