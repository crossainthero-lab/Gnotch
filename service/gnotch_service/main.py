import signal
import sys

from gi.repository import GLib

from .dbus import DBusFrontend


def main():
    loop = GLib.MainLoop()
    fe = DBusFrontend(loop)
    fe.run()

    def stop(*_):
        if fe.service:
            fe.service.shutdown()
        GLib.timeout_add(300, loop.quit)
        return GLib.SOURCE_REMOVE
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
