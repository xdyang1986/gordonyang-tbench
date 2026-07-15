Scenario
The data in the database may be corrupted while writing into a single append-only log file. So we need to implement a tool, let's call it dbfsck -- a command-line utility that ingests one of these log files, salvages the maximum amount of valid data, and summarizes the results.

On-Disk Format
A log file begins with a fixed 8-byte header, followed by zero or more records stored back-to-back without padding, and finally any remaining pre-allocated zero-filled space not yet consumed by writes. All integers are unsigned, 32-bit, little-endian.

Header (8 bytes):
0–3	ASCII magic: DBLG
4–7	Format version (uint32 LE) — must equal 1

Record layout (repeating):

Field	Size	        Description
key_len	4 bytes	        Key length
val_len	4 bytes	        Value length
key	    key_len bytes	Opaque key data (may contain NUL, tabs, newlines, or bytes resembling the header magic)
val	    val_len bytes	Opaque value data (may be empty; may contain or end with NUL bytes)
crc	    4 bytes	        CRC-32 using the IEEE polynomial (equivalent to Go's crc32.ChecksumIEEE), computed over every byte of the record excluding the CRC field itself—spanning from the start of key_len through the last byte of val

A record is intact at a given offset if its declared total size fits within the bytes remaining in the file and its stored CRC matches a freshly computed checksum over those bytes.

CLI Interface
```
dbfsck --in <PATH> [--out <PATH>]
```

Recovery Logic
Identify all intact records throughout the file, then choose the subset that maximizes the number of recovered records subject to two constraints: (1) no byte in the file is claimed by more than one chosen record, and (2) the chosen records are ordered by their starting offset. If multiple valid subsets tie for the highest count, any one is acceptable.

Throughout the process, treat the file as adversarial input—never read beyond the file boundary, and never trust a length field for memory allocation or pointer advancement without first verifying that the file contains at least that many remaining bytes.

Output
dbfsck emits a single JSON line to stdout:
  ```
  {"recovered":<R>,"skipped":<S>}
  ```

R means total number of records recovered and S means total number of damaged/unrecoverable bytes
If --out is supplied, dbfsck also writes a clean log to that path: the 8-byte header followed by the recovered records in their original file-offset order. When --out is omitted, only the JSON report is produced—no file is written.

Exit Codes
0	File is clean — no bytes were skipped (S == 0)
1	Corruption found — at least one byte was skipped (repaired file written when --out was given)
2	File is unusable — unreadable, shorter than 8 bytes, or missing the correct magic/version. No output file is produced.

Constraints
Go standard library only — no external dependencies.
Must compile successfully via go build ./... from /app/src/ without network access.

Deliverable
Implement dbfsck under /app/src/ (with go.mod and package main).
