r"""Disable ZCode workspace-snapshot upload at the source.

Replaces every occurrence of the upload-credential endpoint string inside
app.asar with a same-length, non-existent route. The client's
captureBeforePromptUnsafe() aborts BEFORE any packaging when the server
returns 404 for the credential request, so nothing is ever tarred,
encrypted, written to disk or uploaded.

Same-length, in-place edit keeps the asar index header untouched
(offsets/sizes unchanged), which is what asar integrity validation (if
enabled) checks.

Usage:
    python patch_asar.py --check    read-only: report occurrence counts
    python patch_asar.py            backup + patch + verify (needs admin)
"""
import os
import shutil
import sys


def find_asar():
    """Locate app.asar across install layouts; --asar PATH or ZCODE_ASAR env override."""
    if "--asar" in sys.argv:
        i = sys.argv.index("--asar")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    cands = [
        os.environ.get('ZCODE_ASAR'),
        r"C:\Program Files\ZCode\resources\app.asar",
        r"C:\Program Files (x86)\ZCode\resources\app.asar",
        os.path.join(os.environ.get('LOCALAPPDATA', ''),
                     'Programs', 'ZCode', 'resources', 'app.asar'),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


ASAR = find_asar()
BAK = ASAR + ".original-backup" if ASAR else None
PAT = b"/api/v1/snapshot/upload-credential"
REP = b"/api/v1/snapshot/xpload-credential"   # same 34 bytes, route 404s

assert len(PAT) == len(REP) == 34


def read():
    with open(ASAR, "rb") as f:
        return f.read()


def main():
    dry = "--check" in sys.argv
    if not ASAR:
        print("ERROR: app.asar not found. Searched:")
        print("  C:\\Program Files\\ZCode\\resources\\app.asar")
        print("  C:\\Program Files (x86)\\ZCode\\resources\\app.asar")
        print("  %%LOCALAPPDATA%%\\Programs\\ZCode\\resources\\app.asar")
        print("Specify manually:  python patch_asar.py --asar \"C:\\path\\to\\app.asar\"")
        return 1
    data = read()
    n, done = data.count(PAT), data.count(REP)
    print("endpoint occurrences : %d" % n)
    print("already patched      : %d" % done)
    if dry:
        return 0
    if n == 0:
        print(done and "ALREADY PATCHED - nothing to do" or
              "PATTERN NOT FOUND - app version changed, refusing to touch file")
        return 0 if done else 1

    # The current file is pristine (n>0): always back up THIS version, so a
    # later rollback never downgrades a newer ZCode to an older asar after
    # the app updated and the patch was re-applied.
    shutil.copy2(ASAR, BAK)
    print("backup refreshed (pristine version):", BAK)

    size_before = os.path.getsize(ASAR)
    patched = data.replace(PAT, REP)
    tmp = ASAR + ".patching"
    try:
        with open(tmp, "wb") as f:
            f.write(patched)
        os.replace(tmp, ASAR)
    except PermissionError:
        print("ERROR: no write access to Program Files - run as administrator")
        return 1

    chk = read()
    ok = (chk.count(REP) == done + n and chk.count(PAT) == 0
          and os.path.getsize(ASAR) == size_before)
    print("patched now          : %d" % n)
    print("verify re-read       : endpoint=%d  patched=%d  size_same=%s"
          % (chk.count(PAT), chk.count(REP),
             os.path.getsize(ASAR) == size_before))
    print("RESULT:", "PATCH OK" if ok else "PATCH FAILED - restore the backup!")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
