import os
import subprocess
import sys

GUARD = r'''
import os, shutil, signal, stat, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
folder = os.fsdecode(sys.stdin.buffer.read())
fifo_fds = []
for parent, _, names in os.walk(folder):
    for name in names:
        path = os.path.join(parent, name)
        try:
            if stat.S_ISFIFO(os.lstat(path).st_mode):
                fifo_fds.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
        except OSError:
            pass
for _ in range(50):
    shutil.rmtree(folder, ignore_errors=True)
    if not os.path.lexists(folder):
        break
    time.sleep(0.1)
for fd in fifo_fds:
    os.close(fd)
'''


def remove_folder_when_test_ends(test, folder):
    guard = subprocess.Popen([sys.executable, "-c", GUARD], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    guard.stdin.write(os.fsencode(str(folder)))
    guard.stdin.flush()
    test.addCleanup(guard.communicate)
