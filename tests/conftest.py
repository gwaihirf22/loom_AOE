"""
Shared setup for the tests.

pytest puts the test file's own folder on the import path, not the project
root, so `import loom` would fail without this. Adding the project root
explicitly is the least surprising fix.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
