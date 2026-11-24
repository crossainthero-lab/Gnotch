# Recording the isolated GNOME Shell session

`tests/record.py` records a short mp4 (H.264, yuv420p, constant 30 fps) of the private headless
session created by `tests/shellsession.py`. It never touches the real desktop.

```python
import sys, time; sys.path.insert(0, 'tests')
from shellsession import Session
from record import record, extract_frames

with Session() as s:
    with record(s, 'out/notch.mp4', fps=30, region=None) as rec:   # region=(x, y, w, h) crops
        s.click(960, 10); time.sleep(1.2)
        s.key('Escape');  time.sleep(1.0)
    print(rec.info)        # codec, width, height, fps, frames, duration, wall_seconds
    extract_frames('out/notch.mp4', 'out/frames', [0.2, 1.3, 2.8])   # PNGs for review

    s.record_steps('out/notch2.mp4', [lambda: s.click(960, 10), lambda: s.key('Escape')], settle=1.0)
```

## How it works

Method (a): the `org.gnome.Shell.Screencast` service on the private bus (`Screencast` /
`ScreencastArea`, options `framerate`, `draw-cursor`). It works as is in the private session.
This machine has `pipewiresrc` and `vp8enc` but no `x264enc`/`openh264enc`, so the shell writes
VP8/WebM. That stream is damage driven (idle time produces no frames) with real timestamps; on
stop, ffmpeg resamples it to a true constant fps and encodes libx264, holding the last frame so the
video length equals the wall-clock length of the `with` block. Animation speed is therefore real.

## Limits

- Needs `ffmpeg` with libx264 and `ffprobe` on PATH.
- `record()` waits 0.4 s after start for the first frame, and 0.3 s before stop to flush.
- Odd region sizes are scaled to even width/height (H.264 yuv420p).
- Compositor frames are only produced when something changes, so the real animation resolution is
  the shell's frame rate (typically ~60 Hz in the virtual monitor, but headless can be lower
  under load); 30 fps sampling can skip intermediate compositor frames.
- Fallbacks (b) Mutter ScreenCast + gst-launch and (c) repeated Shell.Screenshot were not needed
  and are not implemented.
