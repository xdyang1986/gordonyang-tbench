  - What to build: a CLI tool to copy a file between database copies — locally and over the network. It can be used when a new copy is created.
  - Requirements:
    - Local copy:
        1. byte-exact.
        2. it handles large files without loading into memory.
        3. it needs to pre-checks free space, creates dest dir / overwrites.
    - Network transfer:
        1. Include sender and receiver side.
        2. The data needs to be compressed in transit
        3. Has space pre-check, no corrupt file on failure
        4. Testable on loopback.
    - General: clean errors + non-zero exit.

  Please build the CLI tool. And please name it as copy_data.py for easy test, and the tests will be like below:

  python copy_data.py local SRC DST [--chunk-size N]
  python copy_data.py send HOST PORT SRC [--codec NAME] [--chunk-size N]
  python copy_data.py recv HOST PORT DST [--chunk-size N]
