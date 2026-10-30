#!/usr/bin/env python
"""Retry classification for objects a classifier outage left pending.

Equivalent to `subzero reclassify`. Safe to run on a schedule.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from subzero.cli import main

    sys.argv = ["subzero", "reclassify", *sys.argv[1:]]
    main()
