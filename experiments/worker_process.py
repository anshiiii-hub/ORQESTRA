"""Use real process signals and production transactions, without a heartbeat side loop."""

import sys
import time

from app.worker import recover_running_tasks, tick

recover_running_tasks()
while True:
    if not tick(sys.argv[1]):
        time.sleep(0.02)
