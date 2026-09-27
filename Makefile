CXX ?= g++
CXXFLAGS ?= -std=c++17 -O3 -Wall -Wextra -pedantic
SOURCE := src/experiment.cpp

ifeq ($(OS),Windows_NT)
TARGET ?= build/p0_maxcut.exe
LDLIBS ?= -lpsapi
MKDIR_BUILD = if not exist build mkdir build
CLEAN_TARGET = del /q "$(TARGET)"
else
TARGET ?= build/p0_maxcut
LDLIBS ?=
MKDIR_BUILD = mkdir -p build
CLEAN_TARGET = rm -f "$(TARGET)"
endif

.PHONY: all build clean

all: build

build: $(TARGET)

$(TARGET): $(SOURCE)
	@$(MKDIR_BUILD)
	$(CXX) $(CXXFLAGS) $< $(LDLIBS) -o $@

clean:
	-$(CLEAN_TARGET)
