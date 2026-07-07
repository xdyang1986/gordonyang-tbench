Background
You're on call for a storage service that persists all its data in a single append-only log file. Each write appends one record, and every record includes a checksum for later integrity verification. After a power failure during a write last night, the log is damaged in several places: a flipped byte here, a corrupted length field there, and a partially written record at the tail. The vast majority of the data is still intact and needs to be recovered.

Build dbfsck, a command-line tool that reads one of these log files, recovers as much valid data as possible, and reports what it found.

File Structure
A log file consists of a fixed 8-byte header followed by zero or more records packed contiguously (no inter-record padding). All integer fields are unsigned, 32-bit, little-endian.

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
crc	4 bytes	CRC-32 (IEEE polynomial, matching Go's crc32.ChecksumIEEE), computed over every byte of the record except the CRC itself—i.e., from key_len through the end of val
A record is intact at a given position if the record it describes fits entirely within the remaining file bytes and its stored CRC matches a fresh checksum of those bytes.

CLI Interface
dbfsck --in <PATH> [--out <PATH>]
Recovery Strategy
Recover the largest possible number of non-overlapping intact records, taken in file-offset order (no byte may belong to more than one recovered record).

Important nuance: Corruption can leave the data such that a smaller intact record sits entirely within the byte span of a larger intact record. In these cases, maximizing the recovery count may require skipping the larger record in favor of multiple smaller ones nested inside it. If two different selections yield the same maximum count, either is acceptable.

Treat the file as untrusted input throughout: never read beyond the file's end, and never use a length field to size a buffer without first verifying that many bytes actually remain.

Output
dbfsck prints exactly one line of JSON to stdout:
{"recovered":<R>,"skipped":<S>}
R — number of records recovered

S — number of bytes not covered by any recovered record

If --out is provided, dbfsck additionally writes a repaired log at that path: the standard 8-byte header followed by the recovered records in file-offset order. If --out is omitted, no file is written—only the JSON report is produced.

Exit Codes
Code	Meaning
0	File is clean — nothing was skipped (S == 0)
1	Corruption detected — at least one byte was skipped (repaired file written if --out was supplied)
2	File is unusable — cannot be read, is shorter than 8 bytes, or lacks the correct magic/version. No output file is written.
Constraints
Go standard library only — no third-party dependencies.

Must build cleanly via go build ./... from /app/src/ with no network access.

Deliverable
Implement dbfsck under /app/src/ (including go.mod and package main).
