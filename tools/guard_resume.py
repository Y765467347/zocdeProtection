"""Resume all ZCode processes previously suspended by snapshot_guard.py."""
try:
    import psutil
except ImportError:
    print('psutil not installed - run: python -m pip install --user psutil')
    raise SystemExit(1)

n = 0
for p in psutil.process_iter(['name']):
    try:
        if 'zcode' in (p.info['name'] or '').lower():
            p.resume()
            n += 1
    except Exception:
        pass
print('resumed %d ZCode process(es)' % n)
