r"""mitmproxy addon - content-level exfiltration inspection (layer 2).

For every request to ZCode/Zhipu API hosts, log: body size, count of
file-path-like strings, and file paths never seen in any previous request
(persisted inventory). Alert when a single request carries an abnormal
number of distinct paths (context stuffing) or an abnormal body size
(bulk embedding / chunked repo transfer).

Run via zcode-net-enforce.bat (MITM mode), which chains:
  ZCode -> mitmdump:8766 (inspect) -> net_audit_proxy:8765 (count) -> net
"""
import json
import os
import re
import time
import threading
import ctypes

HOME = os.environ['USERPROFILE']
TOOLS = os.path.join(HOME, '.zcode-tools')
LOG = os.path.join(TOOLS, 'net_content.log')
INVENTORY = os.path.join(TOOLS, 'content_paths.json')

WATCH_HOSTS = ('zcode.z.ai', 'bigmodel.cn', 'zhipuai', 'z.ai',
               'open.bigmodel.cn')
# Calibrated against a real long session: system prompt alone carries
# 100+ legit paths (skills list, memory index, tool defs). Real context
# stuffing shows up as thousands of paths or huge bodies.
MAX_DISTINCT_PATHS = 300         # per request popup threshold
MAX_BODY_KB = 8192               # per request popup threshold
SAVE_EVERY = 20

WIN_PATH = re.compile(r'[A-Za-z]:\\{1,2}[A-Za-z0-9_\-./\\ ]{2,120}')
UX_PATH = re.compile(r'(?:^|["\s,\\[])((?:/[a-z0-9_.\-]+){2,8})', re.I)
FILEISH = re.compile(
    r'\.(py|js|ts|c|cpp|cc|h|hpp|java|go|rs|md|json|yml|yaml|txt|sql|sh|'
    r'html|css|xml|ini|toml|gradle|cs|kt|swift|vue)\b', re.I)


def _log(msg):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '  ' + msg + '\n')
    except OSError:
        pass


def _alert(body):
    def box():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, body, 'ZCode Net Content Audit', 0x1000)
        except Exception:
            pass
    threading.Thread(target=box, daemon=True).start()


class ContentAudit:
    def __init__(self):
        try:
            with open(INVENTORY, encoding='utf-8') as f:
                self.seen = set(json.load(f))
        except Exception:
            self.seen = set()
        self._dirty = 0

    def _save(self):
        try:
            with open(INVENTORY, 'w', encoding='utf-8') as f:
                json.dump(sorted(self.seen)[-50000:], f)
        except OSError:
            pass

    def request(self, flow):
        host = flow.request.pretty_host or ''
        if not any(h in host for h in WATCH_HOSTS):
            return
        try:
            body = flow.request.get_text(strict=False) or ''
        except Exception:
            body = ''
        size = len(body)
        paths = set(WIN_PATH.findall(body))
        for m in UX_PATH.finditer(body):
            paths.add(m.group(1))
        fileish = sorted(p.strip() for p in paths if FILEISH.search(p))[:500]
        fresh = [p for p in fileish if p not in self.seen]
        self.seen.update(fileish)
        self._dirty += 1
        if self._dirty >= SAVE_EVERY:
            self._dirty = 0
            self._save()
        _log('%s %s%s  body=%dKB  paths=%d new=%d%s'
             % (host, flow.request.path[:80], '',
                size // 1024, len(fileish), len(fresh),
                ('  NEW: ' + ' | '.join(fresh[:5]) + (' ...' if len(fresh) > 5 else ''))
                if fresh else ''))
        if len(fileish) > MAX_DISTINCT_PATHS or size > MAX_BODY_KB * 1024:
            m = ('请求内容异常: %s%s\nbody=%dKB, 携带 %d 个文件路径 (阈值 %d/%dKB)\n'
                 '可能存在上下文夹带/批量外传。新路径示例:\n%s\n日志: %s'
                 % (host, flow.request.path[:60], size // 1024, len(fileish),
                    MAX_DISTINCT_PATHS, MAX_BODY_KB,
                    '\n'.join(fresh[:10]), LOG))
            _log('ALERT ' + m.replace('\n', ' '))
            _alert(m)

    def done(self):
        self._save()


addons = [ContentAudit()]
