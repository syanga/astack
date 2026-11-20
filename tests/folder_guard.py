import os
import subprocess
import sys

GUARD = r'''
import os, shutil, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
folder = os.fsdecode(sys.stdin.buffer.read())
for _ in range(50):
    shutil.rmtree(folder, ignore_errors=True)
    if not os.path.lexists(folder):
        break
    time.sleep(0.1)
'''


def remove_folder_when_test_ends(test, folder):
    guard = subprocess.Popen([sys.executable, "-c", GUARD], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    guard.stdin.write(os.fsencode(str(folder)))
    guard.stdin.flush()
    test.addCleanup(guard.communicate)
