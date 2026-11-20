import os
import subprocess
import sys

GUARD = r'''
import os, shutil, signal, sys
for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(number, signal.SIG_IGN)
shutil.rmtree(os.fsdecode(sys.stdin.buffer.read()), ignore_errors=True)
'''


def remove_folder_when_test_ends(test, folder):
    guard = subprocess.Popen([sys.executable, "-c", GUARD], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    guard.stdin.write(os.fsencode(str(folder)))
    guard.stdin.flush()
    test.addCleanup(guard.communicate)
