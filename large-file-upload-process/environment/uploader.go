package main

import "fmt"

// TODO: Implement parallel upload with worker pool
// For parallel N (1-32), use sync.WaitGroup to wait for workers, channel for chunk indices, sync.Mutex for manifest atomic save
// Each worker must open its own os.Open(source) to avoid Seek race (os.File Seek is not thread-safe)
// For each chunk not yet uploaded, try with retries (0-10) exponential backoff: backoff = 100ms * 2^attempt, print RETRY: chunk X attempt Y backoff <dur> to stderr
// On first attempt, check env var INJECT_FAIL_CHUNK - if set to chunk index string, fail first attempt to test retry (documented hook)
// For each attempt: Seek to chunk.Offset, read chunk.Size via io.LimitReader, compute SHA256 and/or MD5 (use CryptoRand? Use sha256.New and md5.New, MultiWriter for both)
// If encryptKey != "": XOR encrypt: enc[i] = orig[i] ^ key[(offsetInChunk+i)%len(key)] before writing
// Write atomically: create tmp file chunk_*.tmp, write encrypted, rename to final chunk path
// Verify: read back chunk file, decrypt if needed, compute checksum(s) of decrypted and compare to expected
// On success: lock mutex, update manifest chunk checksums and uploaded=true, SaveManifest atomically (temp+rename), unlock, print progress "Uploading chunk X/Y (...) worker W"
// After all chunks, assemble via assembleFileFromManifest which must decrypt if needed, streaming with 1MB buffer
// Final checksum verification of assembled file must match source
func UploadFile(sourceFile string, destDir string, chunkSize int64, manifestPath string, parallel int, retries int, checksumAlgo string, encryptKey string) error {
	return fmt.Errorf("not implemented: see TODOs above")
}

func AssembleFromManifest(manifestPath string, outputPath string) error {
	return fmt.Errorf("not implemented")
}
