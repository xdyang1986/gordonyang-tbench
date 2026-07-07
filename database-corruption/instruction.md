  Scenario
  A service stores data in an append-only log file. Files on disk occasionally get
  corrupted — a flipped byte, or a half-written trailing record left behind after a
  crash. Build a command-line tool, dbfsck, that checks such a file for corruption
  and recovers the intact records.

  File format
  A database file is a fixed 8-byte header followed by zero or more records, stored
  back-to-back with no padding. All integers are unsigned, 32-bit, little-endian.

  Header (8 bytes):
      bytes 0–3   the ASCII magic "DBLG"
      bytes 4–7   format version (uint32, little-endian), equal to 1

  Each record, in order:
      key_len     uint32 little-endian
      val_len     uint32 little-endian
      key         key_len bytes (arbitrary bytes; may contain NUL, tab, newline)
      val         val_len bytes (arbitrary bytes; may be empty)
      crc         uint32 little-endian — CRC-32 with the IEEE polynomial (the value
                  produced by Go's hash/crc32 ChecksumIEEE), computed over the
                  record's bytes from the first byte of key_len through the last
                  byte of val, i.e. every byte of the record except the 4 crc bytes.

  Interface
      dbfsck --in <PATH> [--out <PATH>]

  dbfsck reads the file at --in, scans it record by record, and prints a single line
  of JSON to stdout:

      {"valid":<V>,"corrupt":<C>,"truncated":<T>}

  where V is the number of records whose CRC verified, C the number of records that
  were framed but whose CRC did not match, and T is 1 if the file ended with an
  incomplete trailing record and 0 otherwise.

  If --out is given, dbfsck also writes a repaired database to that path: the 8-byte
  header followed by exactly the valid records, in their original order (corrupt and
  truncated records omitted). Without --out, dbfsck only reports; it writes no file.

  Scanning rules
      - Records begin immediately after the 8-byte header. At each position, read
        key_len and val_len; the whole record occupies 8 + key_len + val_len + 4
        bytes.
      - The length prefix defines framing: to reach the next record, advance by the
        current record's full declared size, whether or not its CRC verified. A
        record whose CRC does not match is counted as corrupt and omitted from the
        output, but scanning continues at the next record.
      - If fewer than 8 bytes remain, or the declared record size would run past the
        end of the file, the file ends with an incomplete record: set truncated to 1
        and stop. Never read past the end of the file, and never allocate a buffer
        sized from a length field without first checking it against the bytes that
        actually remain.

  Exit status
      0   the file is clean: no corrupt records and not truncated
      1   corruption was found (and, when --out was given, a repaired file was
          written)
      2   the input is unusable: it cannot be read, is shorter than the 8-byte
          header, or does not begin with the correct magic and version. In this
          case dbfsck writes no output file.

  Constraints
      Go standard library only — no external dependencies.
      Builds with go build ./... from /app/src/ with no network access.

  Task
      Implement dbfsck under /app/src/ (with go.mod and package main) so it behaves
      as described.
