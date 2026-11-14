CXX := clang++
SDK := $(shell xcrun --sdk macosx --show-sdk-path)
CXXFLAGS := -std=c++17 -Wall -Wextra -Wpedantic -O2 -Isrc -isysroot $(SDK) -DSCHEMA_PATH=\"$(CURDIR)/schema.sql\"
LDFLAGS := -isysroot $(SDK)
LIBS := -lsqlite3 -lcurl -lncurses

.PHONY: all test race demo inspect clean

all: build/derby

build:
	mkdir -p build

build/derby: src/book.cpp src/rpc.cpp src/race.cpp src/store.cpp src/tui.cpp src/main.cpp schema.sql | build
	$(CXX) $(CXXFLAGS) -o $@ src/book.cpp src/rpc.cpp src/race.cpp src/store.cpp src/tui.cpp src/main.cpp $(LDFLAGS) $(LIBS)

build/test_book: tests/test_book.cpp src/book.cpp | build
	$(CXX) $(CXXFLAGS) -o $@ tests/test_book.cpp src/book.cpp $(LDFLAGS)

build/test_race: tests/test_race.cpp src/race.cpp src/rpc.cpp src/book.cpp | build
	$(CXX) $(CXXFLAGS) -o $@ tests/test_race.cpp src/race.cpp src/rpc.cpp src/book.cpp $(LDFLAGS) -lcurl

build/test_store: tests/test_store.cpp src/store.cpp src/book.cpp schema.sql | build
	$(CXX) $(CXXFLAGS) -o $@ tests/test_store.cpp src/store.cpp src/book.cpp $(LDFLAGS) -lsqlite3

test: build/test_book build/test_race build/test_store
	./build/test_book
	./build/test_race
	./build/test_store

race: build/derby
	./scripts/race.sh --once

demo: build/derby
	./scripts/race.sh

inspect:
	sqlite3 -header -column derby.sqlite ".read docs/queries.sql"

clean:
	rm -rf build
