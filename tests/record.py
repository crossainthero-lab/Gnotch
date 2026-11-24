"""Short screen recordings of the ISOLATED headless GNOME Shell started by tests/shellsession.py.

    from shellsession import Session
    from record import record, extract_frames

    with Session() as s:
        with record(s, 'out/notch.mp4') as rec:        # fps=30, region=None (whole stage)
            s.click(960, 10); time.sleep(1.2); s.key('Escape'); time.sleep(1.0)
        print(rec.info)
        extract_frames('out/notch.mp4', 'out/frames', [0.2, 0.6, 1.4])

    s.record_steps('out/notch.mp4', [lambda: s.click(960, 10), lambda: s.key('Escape')], settle=1.0)

Method: the org.gnome.Shell.Screencast D-Bus service of the PRIVATE session bus (GStreamer + PipeWire
screen cast of the private Mutter; the user's real session is never involved). The machine has no x264enc
GStreamer element, so the shell records VP8/WebM, which is damage driven (frames carry real presentation
timestamps, idle periods produce no frames). ffmpeg then resamples to a true constant `fps` and encodes
H.264 (libx264, yuv420p) in an MP4, padded with the last frame to the real wall-clock length of the block,
so animation speed in the video is true.
"""
import contextlib
import json
import os
import subprocess
import time

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # noqa: E402

__all__ = ['record', 'Recorder', 'extract_frames', 'probe_video']


def probe_video(path):
    """ffprobe summary: width, height, fps, duration, frames, codec."""
    out = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                          'stream=codec_name,width,height,avg_frame_rate,nb_read_frames,duration:format=duration',
                          '-of', 'json', path], capture_output=True, text=True, timeout=60, check=True).stdout
    d = json.loads(out)
    st = d['streams'][0]
    n, den = st['avg_frame_rate'].split('/')
    return {'codec': st['codec_name'], 'width': st['width'], 'height': st['height'],
            'fps': round(int(n) / int(den), 3), 'frames': int(st['nb_read_frames']),
            'duration': float(st.get('duration') or d['format']['duration'])}


class Recorder:
    def __init__(self, session, path, fps=30, region=None, draw_cursor=True, crf=18, startup=0.4):
        self.session, self.path, self.fps, self.region = session, os.path.abspath(path), fps, region
        self.draw_cursor, self.crf, self.startup = draw_cursor, crf, startup
        self.info = None
        self.raw = None
        self._t0 = self._t1 = None
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _proxy(self):
        return Gio.DBusProxy.new_sync(self.session._conn, Gio.DBusProxyFlags.NONE, None, 'org.gnome.Shell.Screencast',
                                      '/org/gnome/Shell/Screencast', 'org.gnome.Shell.Screencast', None)

    def start(self):
        self.raw = self.path + '.raw.webm'
        if os.path.exists(self.raw):
            os.unlink(self.raw)
        self._proxy_obj = p = self._proxy()
        opts = {'framerate': GLib.Variant('u', int(self.fps)), 'draw-cursor': GLib.Variant('b', bool(self.draw_cursor))}
        if self.region:
            x, y, w, h = self.region
            args, sig = (x, y, w, h, self.raw, opts), '(iiiisa{sv})'
            name = 'ScreencastArea'
        else:
            args, sig, name = (self.raw, opts), '(sa{sv})', 'Screencast'
        ok, used = p.call_sync(name, GLib.Variant(sig, args), Gio.DBusCallFlags.NONE, 20000, None).unpack()
        if not ok:
            raise RuntimeError('Screencast refused to start')
        self.raw = used
        time.sleep(self.startup)      # let the pipeline deliver its first frame before the caller acts
        self._t0 = time.monotonic()
        return self

    def stop(self):
        self._t1 = time.monotonic()
        time.sleep(0.3)               # flush the last damage frame
        try:
            self._proxy_obj.call_sync('StopScreencast', None, Gio.DBusCallFlags.NONE, 20000, None)
        finally:
            t0 = time.time()
            while time.time() - t0 < 10 and not (os.path.exists(self.raw) and os.path.getsize(self.raw) > 0):
                time.sleep(0.1)
            time.sleep(0.3)           # webm trailer / cues
        wall = self._t1 - self._t0
        tmp = self.path + '.part.mp4'
        # resample variable-frame-time VP8 to constant fps; hold the last frame up to the wall-clock length
        vf = f'fps={self.fps}:round=near,tpad=stop_mode=clone:stop_duration={wall + 1:.3f},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p'
        r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', self.raw, '-vf', vf, '-t', f'{wall:.3f}',
                            '-r', str(self.fps), '-c:v', 'libx264', '-preset', 'medium', '-crf', str(self.crf),
                            '-movflags', '+faststart', '-f', 'mp4', tmp], capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise RuntimeError('ffmpeg failed: ' + r.stderr[-800:])
        os.replace(tmp, self.path)
        os.unlink(self.raw)
        self.info = probe_video(self.path)
        self.info['wall_seconds'] = round(wall, 3)
        return self.info

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, *a):
        try:
            self.stop()
        except Exception:
            if exc_type is None:
                raise


def record(session, path, fps=30, region=None, **kw):
    """Context manager: `with record(session, 'x.mp4') as rec:` ... file is finished on exit (rec.info)."""
    return Recorder(session, path, fps=fps, region=region, **kw)


def record_steps(self, path, steps, settle=1.0, fps=30, region=None, pause=0.0):
    """Record while calling each callable in `steps`; `settle` seconds of quiet follow each step."""
    with record(self, path, fps=fps, region=region) as rec:
        for step in steps:
            step()
            time.sleep(settle + pause)
    return rec.info


def extract_frames(video, outdir, times):
    """Write one PNG per time (seconds) into outdir; returns the list of paths."""
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for t in times:
        p = os.path.join(outdir, f'frame-{t:07.3f}s.png')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', video, '-frames:v', '1', p],
                       check=True, timeout=60)
        paths.append(p)
    return paths


try:   # attach to Session without touching shellsession.py
    from shellsession import Session
    Session.record = lambda self, path, **kw: record(self, path, **kw)
    Session.record_steps = record_steps
except ImportError:
    pass
