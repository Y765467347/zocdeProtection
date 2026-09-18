r"""Network audit proxy - layer: forced-loopback byte accounting.

A transparent CONNECT/HTTP tunnel that NEVER decrypts traffic: it peeks the
TLS ClientHello for the SNI (target hostname), tunnels the bytes, and keeps
per-host hourly/daily byte counters. Stealth exfiltration hides in CONTENT,
not in VOLUME: a repo trickled out in 100 KB chunks still accumulates to
hundreds of MB of upload that no normal chat session produces.

Alerts (popup + log) on:
  - any host uploading  > HOURLY_UP_LIMIT   bytes in one hour
  - all hosts uploading > DAILY_UP_LIMIT    bytes in one day

Usage:
  python net_audit_proxy.py            run (use pythonw for windowless)
  python net_audit_proxy.py --stats    show counters
  python net_audit_proxy.py --health   exit 0 if listening
"""
import asyncio
import json
import os
import sys
import time
import ctypes
import threading
import struct

HOME = os.environ['USERPROFILE']
TOOLS = os.path.join(HOME, '.zcode-tools')
LOG = os.path.join(TOOLS, 'net_audit.log')
STATS = os.path.join(TOOLS, 'net_stats.json')
CONFIG = os.path.join(TOOLS, 'net_audit_config.json')

HOST = '127.0.0.1'
PORT = 8765
BUF = 65536

DEFAULT_CONFIG = {
    'hourly_up_limit': 150 * 1024 * 1024,   # per host
    'daily_up_limit': 600 * 1024 * 1024,    # all hosts combined
    'ignore_hosts': ['127.0.0.1', 'localhost'],
}


def log(msg):
    try:
        os.makedirs(TOOLS, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '  ' + msg + '\n')
    except OSError:
        pass


def load_cfg():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        try:
            os.makedirs(TOOLS, exist_ok=True)
            with open(CONFIG, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except OSError:
            pass
        return dict(DEFAULT_CONFIG)


def alert(body):
    def box():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, body, 'ZCode Net Audit', 0x1000)
        except Exception:
            pass
    threading.Thread(target=box, daemon=True).start()


class Counters:
    def __init__(self):
        try:
            with open(STATS, encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}
        self.alerted = set()

    def bucket(self, host):
        day = time.strftime('%Y-%m-%d')
        hour = time.strftime('%Y-%m-%d %H:00')
        d = self.data.setdefault(host, {'days': {}, 'hours': {}})
        return d, d['days'].setdefault(day, [0, 0]), d['hours'].setdefault(hour, [0, 0])

    def add(self, host, up, down):
        cfg = load_cfg()
        if any(h in host for h in cfg['ignore_hosts']):
            return
        d, day, hour = self.bucket(host)
        day[0] += up; day[1] += down
        hour[0] += up; hour[1] += down
        self.flush()
        key_h = (host, hour)
        key_d = ('TOTAL', time.strftime('%Y-%m-%d'))
        if hour[0] > cfg['hourly_up_limit'] and key_h not in self.alerted:
            self.alerted.add(key_h)
            m = ('上行流量异常: %s 本小时已上传 %.1f MB (阈值 %.0f MB)\n'
                 '正常会话不会有这么大的上传量——可能存在碎片化外传。\n'
                 '详情: %s' % (host, hour[0] / 1048576,
                               cfg['hourly_up_limit'] / 1048576, LOG))
            log('ALERT ' + m.replace('\n', ' '))
            alert(m)
        total_up = sum(v['days'].get(time.strftime('%Y-%m-%d'), [0, 0])[0]
                       for v in self.data.values())
        if total_up > cfg['daily_up_limit'] and key_d not in self.alerted:
            self.alerted.add(key_d)
            m = ('全网日上传异常: 今日累计已上传 %.1f MB\n阈值 %.0f MB。详情: %s'
                 % (total_up / 1048576, cfg['daily_up_limit'] / 1048576, LOG))
            log('ALERT ' + m.replace('\n', ' '))
            alert(m)

    def flush(self):
        try:
            tmp = STATS + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, STATS)
        except OSError:
            pass


CNT = Counters()


def sni_from_client_hello(data):
    """Minimal TLS ClientHello SNI parser (no external deps)."""
    try:
        if not data or data[0] != 0x16:
            return None
        rec_len = struct.unpack('>H', data[3:5])[0]
        hs = data[5:5 + rec_len]
        if not hs or hs[0] != 0x01:
            return None
        p = 4 + 2 + 32   # handshake hdr(1+3) + version(2) + random(32)
        p += 1 + hs[p]                    # session id
        p += 2 + struct.unpack('>H', hs[p:p + 2])[0]      # cipher suites
        p += 1 + hs[p]                    # compression
        ext_len = struct.unpack('>H', hs[p:p + 2])[0]
        p += 2
        end = min(p + ext_len, len(hs))
        while p + 4 <= end:
            et, el = struct.unpack('>HH', hs[p:p + 4])
            body = hs[p + 4:p + 4 + el]
            if et == 0:                    # server_name
                if len(body) < 5:
                    return None
                ln = struct.unpack('>H', body[3:5])[0]
                return body[5:5 + ln].decode('utf-8', 'ignore')
            p += 4 + el
    except Exception:
        return None
    return None


async def pump(reader, writer, host, direction):
    try:
        while True:
            data = await reader.read(BUF)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            CNT.add(host, len(data) if direction == 'up' else 0,
                    len(data) if direction == 'down' else 0)
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_connect(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if line in (b'\r\n', b'\n', b''):
                break
        # client waits for the CONNECT reply before its TLS handshake
        writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await writer.drain()
        head = await reader.read(BUF)
        sni = sni_from_client_hello(head)
        if not head or not sni:
            writer.close()
            log('no SNI in ClientHello (%d bytes head)' % len(head))
            return
        try:
            up_r, up_w = await asyncio.open_connection(sni, 443)
        except Exception as e:
            writer.close()
            log('connect fail %s: %s' % (sni, e))
            return
        up_w.write(head)
        await up_w.drain()
        CNT.add(sni, len(head), 0)
        await asyncio.gather(pump(reader, up_w, sni, 'up'),
                             pump(up_r, writer, sni, 'down'))
    except Exception as e:
        log('tunnel error: %s' % e)
        try:
            writer.close()
        except Exception:
            pass


async def handle_http(reader, writer):
    try:
        line = await reader.readline()
        parts = line.decode('latin1').split()
        if len(parts) < 2:
            writer.close()
            return
        url = parts[1]
        host = url.split('/')[2] if '://' in url else 'http-unknown'
        try:
            up_r, up_w = await asyncio.open_connection(host, 80)
        except Exception:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            await writer.drain()
            writer.close()
            return
        up_w.write(line)
        while True:
            h = await reader.readline()
            up_w.write(h)
            if h in (b'\r\n', b'\n', b''):
                break
        await up_w.drain()
        await asyncio.gather(pump(reader, up_w, host, 'up'),
                             pump(up_r, writer, host, 'down'))
    except Exception:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader, writer):
    try:
        peek = await reader.readline()
        if peek.startswith(b'CONNECT'):
            # re-inject the method line for handle_connect
            class R:
                def __init__(self, r, first):
                    self._r, self._first = r, first

                async def readline(self):
                    if self._first is not None:
                        v, self._first = self._first, None
                        return v
                    return await self._r.readline()

                def read(self, n=-1):
                    return self._r.read(n)
            await handle_connect(R(reader, peek), writer)
        else:
            class R2:
                def __init__(self, r, first):
                    self._r, self._first = r, first

                async def readline(self):
                    if self._first is not None:
                        v, self._first = self._first, None
                        return v
                    return await self._r.readline()
            await handle_http(R2(reader, peek), writer)
    except Exception:
        pass


async def serve():
    server = await asyncio.start_server(handle, HOST, PORT)
    log('net audit proxy listening on %s:%d' % (HOST, PORT))
    async with server:
        await server.serve_forever()


def show_stats():
    try:
        with open(STATS, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        print('no stats yet')
        return
    day = time.strftime('%Y-%m-%d')
    tot_up = tot_dn = 0
    for host, v in sorted(data.items()):
        d = v['days'].get(day, [0, 0])
        if d[0] or d[1]:
            print('%-40s up %8.2f MB  down %8.2f MB'
                  % (host, d[0] / 1048576, d[1] / 1048576))
            tot_up += d[0]; tot_dn += d[1]
    print('-' * 72)
    print('%-40s up %8.2f MB  down %8.2f MB' % ('TOTAL today', tot_up / 1048576, tot_dn / 1048576))


def main():
    if '--stats' in sys.argv:
        show_stats()
        return 0
    if '--health' in sys.argv:
        import socket
        try:
            s = socket.create_connection((HOST, PORT), timeout=3)
            s.close()
            return 0
        except Exception:
            return 1
    asyncio.run(serve())


if __name__ == '__main__':
    sys.exit(main())
