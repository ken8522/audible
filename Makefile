# Build the native activation-bytes brute-forcer.
#
#   make          -> build native/aax_crack
#   make clean    -> remove the built binary
#
# crack.py also builds this on demand, so running `make` by hand is optional.
# Self-contained: no external libraries (SHA1 is bundled).

CC      ?= cc
BIN      = native/aax_crack
SRC      = native/aax_crack.c

# -O3 for speed, -pthread for the worker threads. -march=native is added if the
# compiler accepts it (falls back gracefully otherwise).
BASE_CFLAGS = -O3 -pthread -Wall -Wextra
MARCH := $(shell $(CC) -march=native -E -x c /dev/null >/dev/null 2>&1 && echo -march=native)
CFLAGS  ?= $(BASE_CFLAGS) $(MARCH)

.PHONY: all clean
all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(BIN)
