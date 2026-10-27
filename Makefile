CC = gcc
CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -O2

.PHONY: all test clean
all: chat

chat: chat.c
	$(CC) $(CFLAGS) $< -o $@

test: chat
	python3 test_chat.py

clean:
	rm -f chat
