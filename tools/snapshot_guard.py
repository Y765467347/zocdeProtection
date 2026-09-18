r"""ZCode snapshot guard - real-time filesystem monitor (layer 4).

Watches %USERPROFILE%\.zcode with an incremental mtime-pruned scanner
(default: every 0.5 s). The moment an encrypted snapshot artifact
(*.enc, *.envelope.json) appears anywhere in the tree it:

  1. suspends every ZCode.exe process  -> kills any in-flight upload,
     because a suspended process cannot stream further bytes;
  2. deletes the artifact (and retries if a handle is still open);
  3. raises a system-modal popup and writes guard.log.

Also verifies once an hour that the checkpoints folder is still empty
and still carries the DENY ACE installed by zcode-block-snapshot.bat.

Runs windowless via pythonw.exe; registered at logon via schtasks.
Self-test (no suspension involved):  python snapshot_guard.py --selftest
"""
import os
import re
import sys
import time
import math
import ctypes
import threading
import subprocess
import msvcrt

HOME = os.environ['USERPROFILE']
ROOT = os.path.join(HOME, '.zcode')
TOOLS = os.path.join(HOME, '.zcode-tools')
LOG = os.path.join(TOOLS, 'guard.log')
CKPT = os.path.join(HOME, '.zcode', 'v2', 'checkpoints')

SCAN_INTERVAL = 0.5          # seconds
FULL_RESCAN_PASSES = 40      # drop the dir cache ~ every 20 s (self-heal)
ACL_CHECK_INTERVAL = 3600.0  # seconds
REACT_COOLDOWN = 10.0        # do not re-alert for the same path more often
SUFFIXES = ('.enc', '.envelope.json')

# Detection must survive renames of BOTH the file suffix and the storage
# directory. Path match is broadened to anything snapshot-flavoured, and
# files there are additionally classified by CONTENT, not just by name:
#   - delete+freeze : known suffix, OR json carrying the envelope signature
#                     (encryptedDataKey + keyWrapAlgorithm - server-compat
#                     constraints far more stable than any filename)
#   - alert only    : repo_snapshot_manifest json (evidence, not payload)
#   - alert only    : >=256KB file with entropy > 7.8 bits/byte (encrypted
#                     or compressed blob under any name) - human decides
PATH_RX = re.compile(r'/(checkpoints|snapshot)')
ENTROPY_MIN_BYTES = 256 * 1024
ENTROPY_THRESHOLD = 7.8

ASAR = r"C:\Program Files\ZCode\resources\app.asar"
ASAR_PAT = b"/api/v1/snapshot/upload-credential"
ASAR_REP = b"/api/v1/snapshot/xpload-credential"

SELFTEST = '--selftest' in sys.argv
ALLOW_SUSPEND = not SELFTEST and '--no-suspend' not in sys.argv

try:
    import psutil
except ImportError:
    psutil = None


def log(msg):
    try:
        os.makedirs(TOOLS, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '  ' + msg + '\n')
    except OSError:
        pass


def entropy_head(path, n=65536):
    try:
        with open(path, 'rb') as f:
            b = f.read(n)
    except OSError:
        return 0.0
    if not b:
        return 0.0
    freq = [0] * 256
    for x in b:
        freq[x] += 1
    e, L = 0.0, len(b)
    for c in freq:
        if c:
            p = c / L
            e -= p * math.log2(p)
    return e


def json_signature(path):
    """'artifact' = encrypted envelope (payload), 'manifest' = file list."""
    try:
        if os.path.getsize(path) > 262144:
            return None
        with open(path, 'rb') as f:
            head = f.read(262144)
    except OSError:
        return None
    if b'encryptedDataKey' in head and b'keyWrapAlgorithm' in head:
        return 'artifact'
    if b'repo_snapshot_manifest' in head:
        return 'manifest'
    return None


def classify_snapshot_file(path):
    """'hit' / 'info' / 'suspect' / None for a file inside a snapshot dir."""
    lp = path.lower().replace('\\', '/')
    if 'selftest' in lp or '/.zcode-tools/' in lp:
        return None
    if not PATH_RX.search(lp):
        return None
    if lp.endswith(SUFFIXES):
        return 'hit'
    if lp.endswith('.json'):
        sig = json_signature(path)
        if sig == 'artifact':
            return 'hit'
        if sig == 'manifest':
            return 'info'
        return None
    try:
        if (os.path.getsize(path) >= ENTROPY_MIN_BYTES
                and entropy_head(path) > ENTROPY_THRESHOLD):
            return 'suspect'
    except OSError:
        pass
    return None


def ignored(path):
    p = path.lower().replace('\\', '/')
    return ('selftest' in p) or ('/.zcode-tools/' in p)


def zcode_processes():
    out, me = [], os.getpid()
    if psutil:
        for p in psutil.process_iter(['name']):
            try:
                name = (p.info['name'] or '').lower()
            except Exception:
                continue
            if p.pid != me and 'zcode' in name:
                out.append(p)
    return out


def suspend_zcode():
    if not ALLOW_SUSPEND:
        return 0
    n = 0
    for p in zcode_processes():
        try:
            p.suspend()
            n += 1
        except Exception:
            pass
    return n


ALERT_TEXT = (
    'ZCode Snapshot Guard: 检测到 ZCode 正在生成工作区快照加密包。\n\n'
    '已采取的动作：冻结全部 ZCode 进程（掐断上传）并删除该加密包。\n\n'
    '恢复使用：运行 C:\\Users\\76546\\Desktop\\test0918\\zcode-guard-resume.bat；\n'
    '更稳妥：任务管理器结束 ZCode 后重启。\n'
    '详情见 C:\\Users\\76546\\.zcode-tools\\guard.log'
)

_alert_showing = {}   # key -> True while its popup is on screen


def alert(body, key=None):
    if os.environ.get('GUARD_SILENT'):
        return                          # test mode: log only, no popup
    key = key or body
    if _alert_showing.get(key):
        return                          # same warning already on screen
    _alert_showing[key] = True

    def box():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, body, 'ZCode Snapshot Guard', 0x1000)  # MB_SYSTEMMODAL
        except Exception:
            pass
        finally:
            _alert_showing[key] = False
    threading.Thread(target=box, daemon=True).start()


# ---------------- incremental scanner ----------------
_cache = {}   # dir -> (mtime, [subdirs])


def scan_once():
    hits, infos, suspects, stack = [], [], [], [ROOT]
    seen = set()          # junction/symlink cycles must not loop forever
    while stack:
        d = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        try:
            m = os.stat(d).st_mtime
        except OSError:
            _cache.pop(d, None)
            continue
        ent = _cache.get(d)
        if ent is not None and ent[0] == m:
            stack.extend(ent[1])
            continue
        try:
            names = os.listdir(d)
        except OSError:
            _cache.pop(d, None)   # gone or not a directory
            continue
        dir_target = bool(PATH_RX.search(d.lower().replace('\\', '/')))
        subs = []
        for n in names:
            full = os.path.join(d, n)
            if dir_target:
                kind = classify_snapshot_file(full)
                if kind == 'hit':
                    hits.append(full)
                elif kind == 'info':
                    infos.append(full)
                elif kind == 'suspect':
                    suspects.append(full)
            try:
                if os.path.isdir(full):
                    subs.append(full)
            except OSError:
                pass
        _cache[d] = (m, subs)
        stack.extend(subs)
    return hits, infos, suspects


_last_react = {}   # path -> monotonic time of last reaction


def react(path):
    now = time.monotonic()
    if now - _last_react.get(path, 0) < REACT_COOLDOWN:
        try:
            os.remove(path)   # quiet retry, no duplicate alert
        except OSError:
            pass
        return
    _last_react[path] = now
    log('DETECTED  ' + path)
    if PATH_RX.search(path.lower().replace('\\', '/')):
        n = suspend_zcode()
        log('suspended %d ZCode process(es)' % n)
    else:
        n = 0
        log('outside snapshot dirs, suspension skipped')
    try:
        os.remove(path)
        log('DELETED   ' + path)
    except OSError as e:
        log('delete deferred (handle open, will retry): %s' % e)
    alert(ALERT_TEXT, key=path)


SUSPECT_TEXT = (
    'ZCode Snapshot Guard: 快照类目录下出现可疑文件（改名/换后缀的加密包？）。\n\n'
    '已自动记录但未删除（避免误伤）：{path}\n\n'
    '请检查该文件来源；确认是快照产物就手动删除并检查防护是否被升级绕过。\n'
    '日志: C:\\Users\\76546\\.zcode-tools\\guard.log'
)


def note_only(path, kind):
    """Evidence/suspects: log + alert, never delete (no false-positive damage)."""
    now = time.monotonic()
    if now - _last_react.get(path, 0) < REACT_COOLDOWN:
        return
    _last_react[path] = now
    log('%-8s %s' % (kind.upper(), path))
    alert(SUSPECT_TEXT.format(path=path), key=path)


def acl_check():
    if os.path.isdir(CKPT):
        try:
            out = subprocess.run(['icacls', CKPT], capture_output=True,
                                 text=True, timeout=15).stdout.upper()
            locked = 'DENY' in out
            try:
                leftovers = os.listdir(CKPT)
            except OSError:
                leftovers = ['<cannot list>']
            if not locked or leftovers:
                log('ACL CHECK FAILED  locked=%s contents=%s' % (locked, leftovers))
                alert('ZCode Snapshot Guard: checkpoints 目录防护异常（DENY 缺失或目录非空），\n'
                      '请重新运行 C:\\Users\\76546\\Desktop\\test0918\\zcode-kill-snapshot-upload.bat。',
                      key='acl')
        except Exception:
            pass
    st = asar_state()
    if st == 'stock':
        log('ASAR CHECK FAILED - upload endpoint is back (app updated?)')
        alert('ZCode Snapshot Guard: 检测到 ZCode 更新，快照上传端点已恢复。\n'
              '请重新运行 C:\\Users\\76546\\Desktop\\test0918\\zcode-kill-snapshot-upload.bat 重新打补丁。',
              key='asar')
    elif st == 'unknown':
        log('ASAR CHECK UNKNOWN - endpoint string no longer matches this build')
        alert('ZCode Snapshot Guard: ZCode 版本变化过大，上传端点字符串已无法识别。\n'
              '可能是端点被改名——请勿假设安全，需重新分析并更新补丁。\n'
              '运行: python C:\\Users\\76546\\.zcode-tools\\patch_asar.py --check',
              key='asar-unknown')


def asar_state():
    """'patched' / 'stock' / 'unknown'.

    'unknown' means the app changed so much that neither the original nor
    the patched endpoint string is present - e.g. a future version renamed
    the API. That must raise a LOUD alert, not a silent all-clear.
    """
    if not os.path.exists(ASAR):
        return 'unknown'
    keep = b''
    ov = len(ASAR_PAT) - 1
    with open(ASAR, 'rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            window = keep + chunk
            if ASAR_PAT in window:
                return 'stock'
            if ASAR_REP in window:
                return 'patched'
            keep = chunk[-ov:] if len(chunk) > ov else window
    return 'unknown'


def selftest():
    os.makedirs(TOOLS, exist_ok=True)
    d = os.path.join(ROOT, 'tmp', 'selftest_guard')
    os.makedirs(d, exist_ok=True)
    dummy = os.path.join(d, 'package.tar.gz.enc')
    with open(dummy, 'wb') as f:
        f.write(b'selftest')
    hits, infos, suspects = scan_once()
    leaked = [h for h in hits if 'selftest' not in h]  # selftest paths must be ignored
    react(dummy)                                        # suspension disabled in selftest
    gone = not os.path.exists(dummy)
    try:
        os.rmdir(d)
    except OSError:
        pass
    ok = gone and not leaked
    print('SELFTEST', 'PASS' if ok else 'FAIL',
          '(deleted=%s, live-hits-leaked=%d)' % (gone, len(leaked)))
    log('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def acquire_lock():
    """Single-instance guard: OS file lock dies with the process."""
    try:
        os.makedirs(TOOLS, exist_ok=True)
        f = open(os.path.join(TOOLS, 'guard.lock'), 'a+')
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            return None
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        return f          # keep the handle (and the lock) for our lifetime
    except OSError:
        return None


def main():
    if '--check-asar' in sys.argv:
        st = asar_state()
        print('asar state:', st)
        return 0 if st == 'patched' else 1
    if SELFTEST:
        return selftest()
    lock = acquire_lock()
    if lock is None:
        log('another guard instance holds the lock, exiting')
        return 0
    log('guard started  pid=%d  psutil=%s  suspend=%s'
        % (os.getpid(), bool(psutil), ALLOW_SUSPEND))
    passes = 0
    last_acl = 0.0
    while True:
        try:
            hits, infos, suspects = scan_once()
            for h in hits:
                react(h)
            for h in infos:
                note_only(h, 'manifest')
            for h in suspects:
                note_only(h, 'entropy')
            passes += 1
            if passes % FULL_RESCAN_PASSES == 0:
                _cache.clear()
            now = time.monotonic()
            if now - last_acl > ACL_CHECK_INTERVAL:
                last_acl = now
                acl_check()
        except Exception as e:
            log('loop error (continuing): %r' % e)
        time.sleep(SCAN_INTERVAL)


if __name__ == '__main__':
    sys.exit(main())
