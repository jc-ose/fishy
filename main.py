"""fishy entry point.

    uv run main.py [project.fishy]   # the app (milestone 2 harness for now)
    uv run python -m fishy.demo      # the milestone 1 layout demo
"""

import sys

from fishy.app import main

if __name__ == "__main__":
    sys.exit(main())
