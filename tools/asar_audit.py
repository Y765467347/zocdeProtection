r"""asar audit - run BEFORE every manual ZCode update is trusted.

Compares the current app.asar against the pristine backup and flags:
  - files added/removed/changed between versions
  - upload-capable primitives (fetch/FormData/XHR/sendBeacon/WebSocket/
    multipart/PostObject) appearing inside CHANGED regions
  - any reference to snapshot-ish endpoints in new code

Usage:
  python asar_audit.py            audit current asar vs original-backup
  python asar_audit.py --scan     grep primitives in current asar (no diff)
"""
import difflib
import json
import os
import re
import struct
import sys

HOME = os.environ['USERPROFILE']
DEFAULT_ASAR = r"C:\Program Files\ZCode\resources\app.asar"


def find_asar():
    if "--asar" in sys.argv:
        return sys.argv[sys.argv.index("--asar") + 1]
    for c in (os.environ.get('ZCODE_ASAR'), DEFAULT_ASAR,
              r"C:\Program Files (x86)\ZCode\resources\app.asar",
              os.path.join(os.environ.get('LOCALAPPDATA', ''),
                           'Programs', 'ZCode', 'resources', 'app.asar')):
        if c and os.path.exists(c):
            return c
    return None


PRIMITIVES = re.compile(
    r'fetch\(|FormData|XMLHttpRequest|sendBeacon|new WebSocket|'
    r'\.upload\(|PostObject|multipart/form-data|ReadableStream|'
    r'getReader\(|ReadabletoBlob|Blob\(', re.I)
SNAPSHOTISH = re.compile(r'snapshot|upload-credential|uploadCredential', re.I)


def parse_asar(path):
    """Return (files_dict, f, base). Header layout (probed):
    [4B]=4 | [4B] header_block_size | [4B] header_block_size-4 | [4B] json_size | json @16
    file content base = 8 + header_block_size."""
    f = open(path, 'rb')
    meta = f.read(16)
    header_block = struct.unpack('<I', meta[4:8])[0]
    json_size = struct.unpack('<I', meta[12:16])[0]
    header = json.loads(f.read(json_size))
    base = 8 + header_block

    files = {}

    def walk(node, prefix):
        for name, meta in node.get('files', {}).items():
            rel = prefix + '/' + name if prefix else name
            if 'files' in meta:
                walk(meta, rel)
            else:
                files[rel] = (int(meta.get('offset', 0)), int(meta.get('size', 0)))

    walk(header, '')
    return files, f, base


def read_file(f, base, off, size):
    f.seek(base + off)
    return f.read(size)


def extract_interesting(path):
    """relpath -> text for JS-ish bundles worth auditing."""
    files, f, base = parse_asar(path)
    out = {}
    for rel, (off, size) in files.items():
        if rel.endswith(('.js', '.cjs', '.mjs', '.json')) and size > 1024:
            out[rel] = read_file(f, base, off, size)
    f.close()
    return out


def main():
    scan_only = '--scan' in sys.argv
    cur = find_asar()
    if not cur:
        print('app.asar not found')
        return 1
    bak = cur + '.original-backup'
    print('current :', cur)
    print('backup  :', bak if os.path.exists(bak) else '(none)')
    print()

    cur_files = extract_interesting(cur)
    print('current bundle: %d js/json files, %.1f MB'
          % (len(cur_files), sum(len(v) for v in cur_files.values()) / 1048576))

    def report_hits(label, texts):
        findings = 0
        for rel, blob in sorted(texts.items()):
            try:
                text = blob.decode('utf-8', 'replace')
            except Exception:
                continue
            hits = list(PRIMITIVES.finditer(text))
            snap = list(SNAPSHOTISH.finditer(text))
            if hits or snap:
                findings += 1
                print('  %-50s primitives=%-4d snapshot-refs=%d'
                      % (rel[:50], len(hits), len(snap)))
        print('%s: %d/%d files contain primitives (baseline indicator)' %
              (label, findings, len(texts)))
        return findings

    if scan_only or not os.path.exists(bak):
        report_hits('SCAN', cur_files)
        return 0

    bak_files = extract_interesting(bak)
    added = set(cur_files) - set(bak_files)
    removed = set(bak_files) - set(cur_files)
    changed = {r for r in set(cur_files) & set(bak_files)
               if cur_files[r] != bak_files[r]}
    print('vs backup: added=%d removed=%d changed=%d' %
          (len(added), len(removed), len(changed)))
    if added:
        for r in sorted(added)[:30]:
            print('  +', r)
    if removed:
        for r in sorted(removed)[:30]:
            print('  -', r)
    print()

    suspicious = 0
    for rel in sorted(changed):
        old = bak_files[rel].decode('utf-8', 'replace').splitlines()
        new = cur_files[rel].decode('utf-8', 'replace').splitlines()
        sm = difflib.SequenceMatcher(None, old, new, autojunk=True)
        added_lines = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ('insert', 'replace'):
                added_lines.extend(new[j1:j2])
        joined = '\n'.join(added_lines)
        p_hits = len(list(PRIMITIVES.finditer(joined)))
        s_hits = len(list(SNAPSHOTISH.finditer(joined)))
        if p_hits or s_hits:
            suspicious += 1
            print('CHANGED+PRIMITIVES: %s  (+%d lines, prim=%d, snapshot=%d)'
                  % (rel[:60], len(added_lines), p_hits, s_hits))
            for line in added_lines:
                if PRIMITIVES.search(line) or SNAPSHOTISH.search(line):
                    print('    |', line.strip()[:160])
                    if sum(1 for _ in [0]) and len(added_lines) > 400:
                        break
    print()
    print('RESULT:', '%d changed files add upload-capable code' % suspicious,
          '- REVIEW MANUALLY before trusting the update' if suspicious
          else '- no upload primitives added in changed regions (good sign)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
