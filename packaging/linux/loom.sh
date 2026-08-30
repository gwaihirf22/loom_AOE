#!/bin/sh
# Loom's entry point inside the Flatpak.
#
# The launcher starts the overlay, the coach and the APM counter as child
# processes, and loom/entry.py builds those command lines from
# sys.executable. Unfrozen - which a Flatpak is, because the runtime brings
# a real interpreter and nothing here is built by PyInstaller - that is the
# interpreter running this script, so the children inherit the right one for
# free. loom/runner.py sets their working directory to paths.PROJECT_ROOT,
# which is why `-m tools.apm_counter` resolves.
exec python3 /app/share/loom/loom_app.py "$@"
