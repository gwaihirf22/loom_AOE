"""
Shared setup for the tests.

pytest puts the test file's own folder on the import path, not the project
root, so `import loom` would fail without this. Adding the project root
explicitly is the least surprising fix.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
