"""Entry point for the frozen Windows build.

PyInstaller runs its start script as ``__main__``, so pointing it straight at
``omega/desktop.py`` detaches that file from its package and every relative
import inside it fails with "attempted relative import with no known parent
package". This thin launcher keeps ``omega`` a properly imported package and
leaves ``omega.desktop`` importable from source in exactly the same way.
"""

from __future__ import annotations

import multiprocessing
import sys


def _main() -> int:
    # The walk-forward optimiser uses a process pool. Under a frozen build the
    # child re-executes this very executable, and without this call it would
    # start a second copy of the whole application instead of a worker.
    multiprocessing.freeze_support()

    from omega.desktop import main

    return main()


if __name__ == "__main__":
    sys.exit(_main())
