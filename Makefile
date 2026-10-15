.PHONY: test shell-test install uninstall
test:
	dbus-run-session -- python3 -W ignore -m unittest discover -s tests -v

shell-test:
	sh tests/shell-test.sh

install:
	packaging/install.sh

uninstall:
	packaging/install.sh --uninstall
