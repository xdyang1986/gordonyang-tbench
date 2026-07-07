Background
A service persists data to an append-only log file. These files are susceptible to on-disk corruption—bit flips, garbled length fields, or incomplete trailing records from unclean shutdowns. Your job is to build dbfsck, a command-line tool that detects corruption in such files and salvages as many intact records as it can.

File Structure
A database file consists of a fixed 8-byte header followed by zero or more tightly packed records (no inter-record padding). All integer fields are unsigned, 32-bit, little-endian.

Header (8 bytes):

Offset	Content
0–3	ASCII magic bytes: DBLG
4–7	Format version (uint32, little-endian) — must be 1
Record layout (repeating):

Field	Size	Description
key_len	4 bytes	Length of the key (uint32 LE)
val_len	4 bytes	Length of the value (uint32 LE)
key	key_len bytes	Arbitrary byte sequence (may include NUL, tabs, newlines)
val	val_len bytes	Arbitrary byte sequence (may be empty)
crc	4 bytes	CRC-32 (IEEE polynomial, as produced by Go's crc32.ChecksumIEEE), computed over all preceding bytes in the record—from the start of key_len through the end of val
A record is valid at a given offset if:

Its total size (8 + key_len + val_len + 4) does not exceed the remaining bytes in the file, and

Its crc matches the checksum of the bytes it covers.

CLI Interface
text

Copy
dbfsck --in <PATH> [--out <PATH>]
Recovery Algorithm
Starting immediately after the header, scan forward through the file:

If a valid record starts at the current position, recover it and advance past it.

Otherwise, skip forward one byte and try again. Each byte skipped this way counts toward the "skipped" total.

This byte-by-byte resynchronization means a single corrupted region (even a mangled length field) does not doom the remainder of the file—subsequent valid records will still be found. Important: never read beyond the file's end, and never trust a length field without first confirming that many bytes actually remain.

Output
dbfsck prints exactly one line of JSON to stdout:

json

Copy
{"recovered":<R>,"skipped":<S>}
R — number of records successfully recovered

S — total number of bytes skipped

If --out is provided, dbfsck additionally writes a repaired file at that path: the standard 8-byte header followed by the recovered records in discovery order. If --out is omitted, no file is written.

Exit Codes
Code	Meaning
0	File is clean — zero bytes skipped
1	Corruption detected — at least one byte was skipped (repaired file written if --out was supplied)
2	Input is unusable — file is unreadable, shorter than 8 bytes, or has an invalid magic/version. No output file is written in this case.
Constraints
Go standard library only — no third-party dependencies.

Must build cleanly via go build ./... from /app/src/ with no network access.

Deliverable
Implement dbfsck under /app/src/ (including go.mod and a package main) conforming to the behavior described above.
