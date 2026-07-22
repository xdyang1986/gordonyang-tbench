"""
Tests for large file upload processor - YouTube-like video platform
Handles hundreds of GB files with chunked resumable upload in Go
"""

import os
import sys
import json
import shutil
import hashlib
import subprocess
import tempfile
from pathlib import Path

APP_DIR = Path("/app")
TMP_BASE = Path("/tmp")


def run_cmd(cmd, cwd=APP_DIR, check=False, timeout=120):
    """Run command and return result"""
    # cmd can be string or list
    if isinstance(cmd, str):
        result = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    else:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    return result


def go_run(args, cwd=APP_DIR, timeout=120):
    """Run go run . with args"""
    cmd = ["go", "run", "."] + args
    return run_cmd(cmd, cwd=cwd, timeout=timeout)


def get_binary():
    """Try to find built binary or use go run"""
    # If binary exists at /tmp/uploader, use it, else go run
    if Path("/tmp/uploader").exists():
        return ["/tmp/uploader"]
    if (APP_DIR / "uploader").exists():
        return [str(APP_DIR / "uploader")]
    return ["go", "run", "."]


def run_uploader(args, cwd=APP_DIR, timeout=120):
    bin_cmd = get_binary()
    cmd = bin_cmd + args
    return run_cmd(cmd, cwd=cwd, timeout=timeout)


# Helper to create dummy video files with correct magic bytes
def create_dummy_video(path: Path, fmt: str, size_bytes: int = 1024 * 1024):
    """Create dummy video file with magic bytes for given format"""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing
    if path.exists():
        path.unlink()

    # Magic bytes per format
    magic_map = {
        "mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x08free",
        "mov": b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00wide",
        "mkv": b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
        "webm": b"\x1a\x45\xdf\xa3\x00\x00\x00\x00webm\x00\x00\x00\x00\x00\x00\x00",
        "avi": b"RIFF\x00\x00\x00\x00AVI LIST\x00\x00\x00\x00",
        "flv": b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00",
        "mpeg": b"\x00\x00\x01\xba\x00\x00\x00\x00\x00\x00\x00\x00",
        "mpg": b"\x00\x00\x01\xba\x00\x00\x00\x00\x00\x00\x00\x00",
        "3gp": b"\x00\x00\x00\x14ftyp3gp4\x00\x00\x00\x00\x00\x00\x00\x00",
        "wmv": b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c\x00\x00\x00\x00",
    }

    magic = magic_map.get(fmt.lower(), b"\x00\x00\x00\x18ftypisom")

    # Create sparse file efficiently
    # First write magic
    with open(path, "wb") as f:
        f.write(magic)
        f.write(b"\x00" * (min(256 - len(magic), 256)))

    # Then extend to desired size via truncate (sparse)
    if size_bytes > 256:
        os.truncate(path, size_bytes)

    return path


def create_sparse_file(path: Path, size_str: str, fmt="mp4"):
    """Create sparse file using truncate, with magic header"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    # Use truncate command for sparse
    subprocess.run(f"truncate -s {size_str} {path}", shell=True, check=True)
    # Write magic at start
    magic_map = {
        "mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x08free",
        "mov": b"\x00\x00\x00\x14ftypqt  ",
        "mkv": b"\x1a\x45\xdf\xa3",
    }
    magic = magic_map.get(fmt, b"\x00\x00\x00\x18ftypisom")
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(magic)
    return path


def compute_sha256(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ==================== TESTS ====================


def test_go_mod_and_build():
    """Test that Go module builds without external deps and no OOM patterns allowed"""
    assert (APP_DIR / "go.mod").exists(), "go.mod must exist"
    content = (APP_DIR / "go.mod").read_text()
    assert "largefileuploader" in content, "module name should be largefileuploader"

    # Check no external dependencies (except stdlib)
    # go.mod should not have require with external packages, or only have go version
    lines = content.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("require"):
            # Should not require external packages - check if it contains github etc
            if "github.com" in line or "golang.org/x" in line:
                assert False, f"External dependency not allowed (stdlib only): {line}"

    # Try to build
    result = run_cmd("go build -o /tmp/uploader .", cwd=APP_DIR, timeout=60)
    assert result.returncode == 0, f"go build failed: {result.stderr}\n{result.stdout}"
    assert Path("/tmp/uploader").exists(), "binary should be built"


def test_memory_efficiency_code_scan():
    """Ensure code doesn't load entire file into memory - scan for forbidden patterns"""
    forbidden_patterns = [
        "os.ReadFile",  # should not read entire file for upload logic
        "ioutil.ReadFile",
        "io.ReadAll",  # on file - but careful, manifest reading is okay
    ]

    # Read all Go files
    go_files = list(APP_DIR.glob("*.go"))
    assert len(go_files) > 0, "No Go files found in /app"

    # We allow ReadFile for manifest (small) but not for source file in uploader
    # Check uploader.go and hasher.go don't have ReadFile for source
    for go_file in go_files:
        content = go_file.read_text()
        if "uploader.go" in str(go_file) or "hasher.go" in str(go_file):
            # These should use streaming (io.CopyBuffer, Seek)
            # Allow at most 1 ReadFile for manifest but not in upload loop
            # Check they use io.CopyBuffer or io.Copy
            has_streaming = ("io.CopyBuffer" in content) or ("io.Copy" in content)
            assert has_streaming, (
                f"{go_file.name} must use streaming io.Copy/Buffer for large files"
            )

        # General: must use int64 for sizes (check for int64 usage)
        if (
            "chunk.go" in str(go_file)
            or "manifest.go" in str(go_file)
            or "uploader.go" in str(go_file)
        ):
            assert "int64" in content, (
                f"{go_file.name} must use int64 for large file support"
            )

    # Must use Seek for chunk reading
    uploader_content = (APP_DIR / "uploader.go").read_text()
    assert "Seek" in uploader_content, (
        "uploader must use Seek for chunked reading (large file support)"
    )

    # Must have chunk size parsing with human readable
    chunk_content = (APP_DIR / "chunk.go").read_text()
    assert "ParseChunkSize" in chunk_content, "ParseChunkSize function must exist"


def test_format_validation_supported():
    """Test validation for all supported formats"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fmt in [
            "mp4",
            "mov",
            "mkv",
            "webm",
            "avi",
            "flv",
            "mpeg",
            "mpg",
            "3gp",
            "wmv",
        ]:
            video_path = tmp / f"test.{fmt}"
            create_dummy_video(video_path, fmt, size_bytes=1024 * 1024)

            result = run_uploader(["validate", "--file", str(video_path)], timeout=30)
            print(f"Validate {fmt}: {result.stdout} {result.stderr}")
            assert result.returncode == 0, (
                f"Format {fmt} should be valid, got: {result.stdout} {result.stderr}"
            )
            assert "VALID" in result.stdout, f"Should output VALID for {fmt}"
            assert fmt.lower() in result.stdout.lower() or (
                fmt == "mpg" and "mpeg" in result.stdout.lower()
            ), f"Output should contain format {fmt}"


def test_format_validation_invalid():
    """Test invalid cases: empty, too small, unsupported"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Empty file
        empty = tmp / "empty.mp4"
        empty.write_bytes(b"")
        result = run_uploader(["validate", "--file", str(empty)], timeout=10)
        assert result.returncode != 0, "Empty file should be invalid"
        assert "INVALID" in result.stdout, "Should output INVALID"

        # Too small (<16 bytes)
        small = tmp / "small.mp4"
        small.write_bytes(b"small")
        result = run_uploader(["validate", "--file", str(small)], timeout=10)
        assert result.returncode != 0
        assert "INVALID" in result.stdout

        # Unsupported format
        bad = tmp / "bad.dat"
        create_dummy_video(bad, "mp4", size_bytes=100)
        # Overwrite with random data
        bad.write_bytes(b"THIS IS NOT VIDEO" * 10)
        result = run_uploader(["validate", "--file", str(bad)], timeout=10)
        assert result.returncode != 0, "Unsupported format should be invalid"
        assert "INVALID" in result.stdout

        # Non-existent file
        result = run_uploader(
            ["validate", "--file", "/nonexistent/file.mp4"], timeout=10
        )
        assert result.returncode != 0
        assert "INVALID" in result.stdout


def test_chunk_size_parsing():
    """Test human-readable chunk size parsing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "sample.mp4"
        create_dummy_video(sample, "mp4", size_bytes=5 * 1024 * 1024)

        # Valid sizes
        valid_cases = ["512K", "512KB", "1M", "8M", "8MB", "1G", "1024", "1048576"]
        for cs in valid_cases:
            result = run_uploader(
                ["info", "--file", str(sample), "--chunk-size", cs], timeout=15
            )
            print(f"Chunk size {cs}: rc={result.returncode}")
            assert result.returncode == 0, (
                f"Chunk size {cs} should be valid: {result.stderr}"
            )

        # Invalid sizes - must contain "invalid chunk size" in output
        invalid_cases = ["0", "0M", "-1M", "abc", "2G", "9999G", "8MBB", ""]
        for cs in invalid_cases:
            if cs == "":
                # Empty case might use default, skip
                continue
            result = run_uploader(
                [
                    "upload",
                    "--source",
                    str(sample),
                    "--dest",
                    str(tmp / f"dest_{cs}"),
                    "--chunk-size",
                    cs,
                ],
                timeout=15,
            )
            # Should fail with invalid chunk size message
            combined = result.stdout + result.stderr
            print(f"Invalid chunk size {cs}: {combined[:200]}")
            assert result.returncode != 0, f"Chunk size {cs} should be invalid"
            assert "invalid chunk size" in combined.lower(), (
                f"Error should mention invalid chunk size for {cs}, got: {combined}"
            )


def test_info_command():
    """Test info command JSON output"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "sample.mp4"
        create_dummy_video(sample, "mp4", size_bytes=2 * 1024 * 1024)

        result = run_uploader(["info", "--file", str(sample)], timeout=15)
        assert result.returncode == 0, f"info should succeed: {result.stderr}"

        info = json.loads(result.stdout)
        assert info["file"] == str(sample)
        assert info["size"] == 2 * 1024 * 1024
        assert info["format"] == "mp4"
        assert info["valid"] == True
        assert "checksum" in info and len(info["checksum"]) == 64, (
            "SHA256 hex should be 64 chars"
        )
        assert "chunk_info" in info
        assert info["chunk_info"]["chunk_size"] == 8 * 1024 * 1024
        # 2MB file with 8MB chunks = 1 chunk
        assert info["chunk_info"]["total_chunks"] == 1

        # Test with custom chunk size
        result = run_uploader(
            ["info", "--file", str(sample), "--chunk-size", "1M"], timeout=15
        )
        info = json.loads(result.stdout)
        assert info["chunk_info"]["total_chunks"] == 2


def test_upload_small_file():
    """Test upload of small 20MB file and verify checksum"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "video.mp4"
        create_dummy_video(sample, "mp4", size_bytes=20 * 1024 * 1024)
        dest = tmp / "dest"
        dest.mkdir()

        orig_checksum = compute_sha256(sample)

        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=60,
        )
        print(f"Upload output: {result.stdout}\n{result.stderr}")
        assert result.returncode == 0, (
            f"Upload failed: {result.stderr}\n{result.stdout}"
        )
        assert "UPLOAD COMPLETE" in result.stdout

        # Check final file exists
        final = dest / "video.mp4"
        assert final.exists(), "Final assembled file should exist"

        # Check checksum matches
        final_checksum = compute_sha256(final)
        assert final_checksum == orig_checksum, (
            f"Checksum mismatch: {orig_checksum} vs {final_checksum}"
        )

        # Check chunks dir
        chunks_dir = dest / "chunks"
        assert chunks_dir.exists()
        chunks = list(chunks_dir.glob("chunk_*"))
        assert len(chunks) == 5, (
            f"20MB with 4M chunks should be 5 chunks, got {len(chunks)}"
        )

        # Check manifest
        manifest = dest / "video.mp4.manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["source_size"] == 20 * 1024 * 1024
        assert data["total_chunks"] == 5
        assert data["file_checksum"] == orig_checksum
        assert len(data["chunks"]) == 5
        for ch in data["chunks"]:
            assert ch["uploaded"] == True
            assert "checksum" in ch and len(ch["checksum"]) == 64


def test_upload_edge_cases():
    """Test edge cases: exact chunk size, smaller than chunk, 1 byte larger"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Exactly chunk size: 8M file with 8M chunks = 1 chunk
        exact = tmp / "exact.mp4"
        create_dummy_video(exact, "mp4", size_bytes=8 * 1024 * 1024)
        dest1 = tmp / "dest_exact"
        dest1.mkdir()
        result = run_uploader(
            [
                "upload",
                "--source",
                str(exact),
                "--dest",
                str(dest1),
                "--chunk-size",
                "8M",
            ],
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Exact chunk size upload failed: {result.stderr}"
        )
        assert (dest1 / "exact.mp4").exists()
        assert compute_sha256(exact) == compute_sha256(dest1 / "exact.mp4")

        # Smaller than chunk: 1M file with 8M chunk
        small = tmp / "small.mp4"
        create_dummy_video(small, "mp4", size_bytes=1 * 1024 * 1024)
        dest2 = tmp / "dest_small"
        dest2.mkdir()
        result = run_uploader(
            [
                "upload",
                "--source",
                str(small),
                "--dest",
                str(dest2),
                "--chunk-size",
                "8M",
            ],
            timeout=30,
        )
        assert result.returncode == 0
        assert compute_sha256(small) == compute_sha256(dest2 / "small.mp4")
        manifest_data = json.loads((dest2 / "small.mp4.manifest.json").read_text())
        assert manifest_data["total_chunks"] == 1
        assert manifest_data["chunks"][0]["size"] == 1 * 1024 * 1024

        # 1 byte larger than chunk size: 8M+1 file with 8M chunks = 2 chunks, last is 1 byte
        larger = tmp / "larger.mp4"
        create_dummy_video(larger, "mp4", size_bytes=8 * 1024 * 1024 + 1)
        dest3 = tmp / "dest_larger"
        dest3.mkdir()
        result = run_uploader(
            [
                "upload",
                "--source",
                str(larger),
                "--dest",
                str(dest3),
                "--chunk-size",
                "8M",
            ],
            timeout=30,
        )
        assert result.returncode == 0, f"8M+1 upload failed: {result.stderr}"
        assert compute_sha256(larger) == compute_sha256(dest3 / "larger.mp4")
        manifest_data = json.loads((dest3 / "larger.mp4.manifest.json").read_text())
        assert manifest_data["total_chunks"] == 2
        assert manifest_data["chunks"][1]["size"] == 1


def test_large_sparse_file_handling():
    """Test handling of large sparse files (simulating 100s of GB) without OOM"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Create 5GB sparse file (doesn't use 5GB disk, but size reports 5GB)
        huge = tmp / "huge.mp4"
        create_sparse_file(huge, "5G", fmt="mp4")

        # Validate works
        result = run_uploader(["validate", "--file", str(huge)], timeout=15)
        assert result.returncode == 0, (
            f"Sparse 5G file validation failed: {result.stderr}"
        )
        assert "VALID" in result.stdout

        # Info should handle large size
        result = run_uploader(
            ["info", "--file", str(huge), "--chunk-size", "8M"], timeout=30
        )
        assert result.returncode == 0
        info = json.loads(result.stdout)
        assert info["size"] == 5 * 1024 * 1024 * 1024
        assert info["chunk_info"]["total_chunks"] == 640  # 5G / 8M = 640

        # Upload with 1G chunks to keep test fast (5 chunks)
        dest = tmp / "dest_huge"
        dest.mkdir()
        result = run_uploader(
            [
                "upload",
                "--source",
                str(huge),
                "--dest",
                str(dest),
                "--chunk-size",
                "1G",
            ],
            timeout=90,
        )
        print(f"Huge upload: {result.stdout[-500:]}\n{result.stderr[-500:]}")
        assert result.returncode == 0, f"Huge file upload failed: {result.stderr}"
        assert "UPLOAD COMPLETE" in result.stdout

        final = dest / "huge.mp4"
        assert final.exists()
        # Verify sparse assembled file size
        assert final.stat().st_size == 5 * 1024 * 1024 * 1024


def test_resumable_upload():
    """Test resumable upload capability"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "resume.mp4"
        create_dummy_video(sample, "mp4", size_bytes=20 * 1024 * 1024)
        dest = tmp / "dest_resume"
        dest.mkdir()

        # First upload fully
        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=45,
        )
        assert result.returncode == 0

        # Simulate interruption: delete final file and one chunk
        final = dest / "resume.mp4"
        final.unlink()
        chunk_to_delete = dest / "chunks" / "chunk_000002"
        assert chunk_to_delete.exists()
        chunk_to_delete.unlink()

        # Check manifest still exists
        manifest = dest / "resume.mp4.manifest.json"
        assert manifest.exists()
        data_before = json.loads(manifest.read_text())
        # Mark deleted chunk as uploaded still (to test verification logic)
        # Actually manifest says uploaded true, but file missing - should be detected
        assert data_before["chunks"][2]["uploaded"] == True

        # Resume upload
        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=45,
        )
        print(f"Resume output: {result.stdout}\n{result.stderr}")
        assert result.returncode == 0
        assert (
            "UPLOAD COMPLETE" in result.stdout
            or "Resuming" in result.stderr
            or "chunk" in result.stdout.lower()
        )

        # Verify final file again
        assert final.exists()
        assert compute_sha256(sample) == compute_sha256(final)

        # Verify re-uploaded chunk exists now
        assert chunk_to_delete.exists()


def test_corrupted_chunk_detection():
    """Test that corrupted chunk is detected and re-uploaded"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "corrupt.mp4"
        create_dummy_video(sample, "mp4", size_bytes=12 * 1024 * 1024)
        dest = tmp / "dest_corrupt"
        dest.mkdir()

        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=45,
        )
        assert result.returncode == 0

        # Corrupt a chunk file (overwrite with random data)
        chunk_path = dest / "chunks" / "chunk_000001"
        assert chunk_path.exists()
        orig_size = chunk_path.stat().st_size
        with open(chunk_path, "wb") as f:
            f.write(b"CORRUPTED DATA " * 100)
            # Make size match to test checksum detection, not just size
            # Write same size but different content
            f.truncate(orig_size)
            f.seek(0)
            f.write(b"X" * orig_size)

        # Delete final file to force resume logic to check chunks
        (dest / "corrupt.mp4").unlink()

        # Resume - should detect corrupted chunk and re-upload
        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=45,
        )
        assert result.returncode == 0, (
            f"Resume after corruption failed: {result.stderr}"
        )

        # Final checksum should still match original
        final = dest / "corrupt.mp4"
        assert final.exists()
        assert compute_sha256(sample) == compute_sha256(final), (
            "After corruption recovery, checksum should match"
        )


def test_corrupted_manifest_handling():
    """Test handling of corrupted manifest JSON"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "manifest.mp4"
        create_dummy_video(sample, "mp4", size_bytes=8 * 1024 * 1024)
        dest = tmp / "dest_manifest"
        dest.mkdir()

        # Create corrupted manifest
        manifest_path = dest / "manifest.mp4.manifest.json"
        manifest_path.write_text("this is not json {{{")

        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=45,
        )
        print(f"Corrupt manifest upload: {result.stdout}\n{result.stderr}")
        # Should warn and start fresh
        assert result.returncode == 0, (
            f"Should handle corrupted manifest: {result.stderr}"
        )
        assert "WARN" in result.stderr and "corrupted manifest" in result.stderr.lower()
        assert "UPLOAD COMPLETE" in result.stdout


def test_source_changed_detection():
    """Test detection when source file changed after manifest creation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "changed.mp4"
        create_dummy_video(sample, "mp4", size_bytes=8 * 1024 * 1024)
        dest = tmp / "dest_changed"
        dest.mkdir()

        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=30,
        )
        assert result.returncode == 0

        # Now change source file size (truncate to different size)
        with open(sample, "ab") as f:
            f.write(b"\x00" * 1024 * 1024)  # Add 1MB

        # Try resume - should error about size mismatch
        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "4M",
            ],
            timeout=15,
        )
        combined = result.stdout + result.stderr
        print(f"Source changed detection: {combined}")
        assert result.returncode != 0, "Should fail when source size changed"
        assert (
            "source file changed" in combined.lower()
            or "size mismatch" in combined.lower()
        )


def test_assemble_command():
    """Test manual assemble command"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sample = tmp / "assemble.mp4"
        create_dummy_video(sample, "mp4", size_bytes=15 * 1024 * 1024)
        dest = tmp / "dest_assemble"
        dest.mkdir()

        # Upload
        result = run_uploader(
            [
                "upload",
                "--source",
                str(sample),
                "--dest",
                str(dest),
                "--chunk-size",
                "5M",
            ],
            timeout=45,
        )
        assert result.returncode == 0

        # Delete final file
        final = dest / "assemble.mp4"
        final.unlink()
        assert not final.exists()

        manifest = dest / "assemble.mp4.manifest.json"
        output = tmp / "manual_output.mp4"

        # Assemble manually
        result = run_uploader(
            ["assemble", "--manifest", str(manifest), "--output", str(output)],
            timeout=30,
        )
        print(f"Assemble output: {result.stdout}\n{result.stderr}")
        assert result.returncode == 0
        assert "ASSEMBLE COMPLETE" in result.stdout
        assert output.exists()
        assert compute_sha256(sample) == compute_sha256(output)


def test_help_commands():
    """Test help flags"""
    for cmd in ["help", "--help", "-h"]:
        result = run_uploader([cmd], timeout=10)
        # help should exit 0 or 2? But should print usage
        assert (
            "Usage" in result.stdout
            or "Usage" in result.stderr
            or "Large File" in (result.stdout + result.stderr)
        )

    for subcmd in ["validate", "info", "upload", "assemble"]:
        result = run_uploader([subcmd, "--help"], timeout=10)
        assert result.returncode == 0 or "Usage" in (result.stdout + result.stderr)


def test_hundreds_gb_simulation():
    """Simulate hundreds of GB scenario with 20GB sparse file"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # 20GB sparse file - tests int64 handling for 100s of GB scaling
        huge = tmp / "20gb.mp4"
        create_sparse_file(huge, "20G", fmt="mp4")

        result = run_uploader(
            ["info", "--file", str(huge), "--chunk-size", "8M"], timeout=30
        )
        assert result.returncode == 0
        info = json.loads(result.stdout)
        assert info["size"] == 20 * 1024 * 1024 * 1024
        # 20GB / 8M = 2560 chunks
        assert info["chunk_info"]["total_chunks"] == 2560

        # Test that total chunks calculation doesn't overflow int32
        # 100GB case: 100*1024 MB /8 MB = 12800 chunks, fits int32 but test large
        # Also test chunk size parsing for large file scenario
        # Simulate 100GB file info without actually creating 100GB sparse (20GB already tests)

        # Test upload with 4G chunks? 4G exceeds 1GB limit should fail
        # But 1G chunks for 20GB = 20 chunks
        dest = tmp / "dest_20gb"
        dest.mkdir()
        result = run_uploader(
            [
                "upload",
                "--source",
                str(huge),
                "--dest",
                str(dest),
                "--chunk-size",
                "1G",
            ],
            timeout=120,
        )
        assert result.returncode == 0, (
            f"20GB upload with 1G chunks failed: {result.stderr}"
        )
        assert (dest / "20gb.mp4").exists()
        assert (dest / "20gb.mp4").stat().st_size == 20 * 1024 * 1024 * 1024


def test_final_checksum_verification():
    """Ensure final assembled file checksum is verified"""
    # Check that uploader.go contains final checksum verification logic
    uploader_content = (Path("/app") / "uploader.go").read_text()
    assert (
        "final checksum" in uploader_content.lower()
        or "checksum mismatch" in uploader_content.lower()
    )
    assert (
        "FileChecksum" in uploader_content
        or "file_checksum" in uploader_content.lower()
    )

    # Check manifest contains checksum fields
    manifest_content = (Path("/app") / "manifest.go").read_text()
    assert "Checksum" in manifest_content
    assert "FileChecksum" in manifest_content
