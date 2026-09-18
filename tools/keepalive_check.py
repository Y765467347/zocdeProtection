"""Keepalive check: start net_audit_proxy.py if port 8765 is down.

Scheduled every 5 minutes (MINUTE trigger). The proxy must be resident
because ZCode's API client is pointed at it via official httpProxy keys -
if it dies, ZCode goes fail-closed (no network) until this task revives it.
"""
import os
import socket
import subprocess
import sys

PORT = 8765
TOOLS = os.path.join(os.environ['USERPROFILE'], '.zcode-tools')


def listening():
    try:
        s = socket.create_connection(('127.0.0.1', PORT), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def main():
    if listening():
        return 0
    pyw = None
    for c in (os.path.join(os.environ.get('LOCALAPPDATA', ''),
                           'Programs', 'Python', 'Python312', 'pythonw.exe'),
              'pythonw'):
        if os.path.exists(c) or c == 'pythonw':
            pyw = c
            break
    if not pyw:
        return 1
    log = os.path.join(TOOLS, 'watchdog.log')
    with open(log, 'a', encoding='utf-8') as f:
        f.write('%s net audit proxy down - keepalive restarting\n' %
                __import__('time').strftime('%Y-%m-%d %H:%M:%S'))
    subprocess.Popen([pyw, os.path.join(TOOLS, 'net_audit_proxy.py')],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
    return 0


if __name__ == '__main__':
    sys.exit(main())
