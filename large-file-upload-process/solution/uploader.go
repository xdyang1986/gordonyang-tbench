package main

import (
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"sync"
	"time"
)

func UploadFile(sourceFile string, destDir string, chunkSize int64, manifestPath string, parallel int, retries int, checksumAlgo string, encryptKey string) error {
	fmt.Fprintf(os.Stderr, "Validating source file...\n")
	if _, err := ValidateVideoFile(sourceFile); err != nil {
		return fmt.Errorf("validation failed: %w", err)
	}

	stat, err := os.Stat(sourceFile)
	if err != nil {
		return fmt.Errorf("source stat failed: %w", err)
	}
	sourceSize := stat.Size()
	if sourceSize == 0 {
		return fmt.Errorf("empty source file")
	}

	fmt.Fprintf(os.Stderr, "Computing source file checksum(s) streaming with algo=%s...\n", checksumAlgo)
	var fileChecksum, fileChecksumMD5 string
	switch checksumAlgo {
	case "sha256":
		fileChecksum, err = ComputeFileSHA256(sourceFile)
		if err != nil {
			return fmt.Errorf("checksum failed: %w", err)
		}
	case "md5":
		fileChecksum, err = ComputeFileMD5(sourceFile)
		if err != nil {
			return fmt.Errorf("checksum failed: %w", err)
		}
	case "both":
		fileChecksum, fileChecksumMD5, err = ComputeFileBoth(sourceFile)
		if err != nil {
			return fmt.Errorf("checksum failed: %w", err)
		}
	default:
		return fmt.Errorf("invalid checksum algo: %s", checksumAlgo)
	}

	var manifest *Manifest

	if _, err := os.Stat(manifestPath); err == nil {
		loaded, err := LoadManifest(manifestPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "WARN: corrupted manifest, starting fresh: %s\n", err.Error())
		} else {
			if loaded.SourceSize != sourceSize {
				return fmt.Errorf("source file changed since manifest creation (size mismatch): manifest %d vs current %d", loaded.SourceSize, sourceSize)
			}
			if loaded.ChecksumAlgo != checksumAlgo {
				fmt.Fprintf(os.Stderr, "WARN: checksum algo changed from %s to %s, using new algo but re-verifying\n", loaded.ChecksumAlgo, checksumAlgo)
				// On algo change, we need to reset uploaded flags because old checksums don't match new algo
				// For simplicity, keep manifest but will re-verify and re-upload missing checksums
			}
			if loaded.EncryptKey != encryptKey {
				if loaded.EncryptKey != "" || encryptKey != "" {
					// If key changed and previous was encrypted or new is encrypted, error
					// Actually if both empty, okay
					if loaded.EncryptKey != encryptKey {
						return fmt.Errorf("encrypt key mismatch: manifest has key %q vs provided %q", loaded.EncryptKey, encryptKey)
					}
				}
			}
			if loaded.ChunkSize != chunkSize {
				fmt.Fprintf(os.Stderr, "WARN: chunk size changed from %d to %d, using manifest's chunk size\n", loaded.ChunkSize, chunkSize)
				chunkSize = loaded.ChunkSize
			}

			fmt.Fprintf(os.Stderr, "Resuming upload session %s with %d workers, verifying existing chunks...\n", loaded.SessionID, parallel)
			for i := range loaded.Chunks {
				if loaded.Chunks[i].Uploaded {
					ok, _ := VerifyChunkFile(loaded.DestDir, loaded.Chunks[i], loaded.EncryptKey, loaded.ChecksumAlgo)
					if !ok {
						fmt.Fprintf(os.Stderr, "Chunk %d corrupted or missing, will re-upload\n", loaded.Chunks[i].Index)
						loaded.Chunks[i].Uploaded = false
					}
				}
			}
			manifest = loaded
			// Update manifest fields to new values where appropriate
			manifest.ChecksumAlgo = checksumAlgo
			manifest.FileChecksum = fileChecksum
			manifest.FileChecksumMD5 = fileChecksumMD5
			manifest.Parallel = parallel
			manifest.EncryptKey = encryptKey
			manifest.DestDir = destDir
		}
	}

	if manifest == nil {
		manifest, err = CreateManifest(sourceFile, sourceSize, chunkSize, destDir, fileChecksum, fileChecksumMD5, checksumAlgo, parallel, encryptKey)
		if err != nil {
			return fmt.Errorf("manifest creation failed: %w", err)
		}
	}

	chunksDir := filepath.Join(destDir, "chunks")
	if err := os.MkdirAll(chunksDir, 0755); err != nil {
		return fmt.Errorf("mkdir dest failed: %w", err)
	}

	if err := SaveManifest(manifest, manifestPath); err != nil {
		return fmt.Errorf("save manifest failed: %w", err)
	}

	// Parallel upload setup
	type job struct {
		index int64
	}

	jobs := make(chan job, manifest.TotalChunks)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var firstErr error
	var firstErrMu sync.Mutex

	// Helper to set first error
	setErr := func(e error) {
		firstErrMu.Lock()
		defer firstErrMu.Unlock()
		if firstErr == nil {
			firstErr = e
		}
	}

	// Worker function
	worker := func(workerID int) {
		defer wg.Done()

		// Each worker opens its own file handle to avoid Seek races
		srcFile, err := os.Open(sourceFile)
		if err != nil {
			setErr(fmt.Errorf("worker %d open source failed: %w", workerID, err))
			return
		}
		defer srcFile.Close()

		buf := make([]byte, 1024*1024) // 1MB buffer per worker

		for j := range jobs {
			chunkIdx := j.index
			// Check if already uploaded (double-check under lock)
			mu.Lock()
			alreadyUploaded := manifest.Chunks[chunkIdx].Uploaded
			mu.Unlock()
			if alreadyUploaded {
				continue
			}

			var lastErr error
			success := false

			for attempt := 0; attempt <= retries; attempt++ {
				if attempt > 0 {
					backoff := time.Duration(100*(1<<uint(attempt-1))) * time.Millisecond
					fmt.Fprintf(os.Stderr, "RETRY: chunk %d attempt %d backoff %v\n", chunkIdx, attempt, backoff)
					time.Sleep(backoff)
				}

				// Seek to offset
				offset := manifest.Chunks[chunkIdx].Offset
				size := manifest.Chunks[chunkIdx].Size

				_, err := srcFile.Seek(offset, io.SeekStart)
				if err != nil {
					lastErr = fmt.Errorf("seek failed for chunk %d: %w", chunkIdx, err)
					continue
				}

				// Read chunk data with limited reader, compute hashes while reading
				// For encryption, we need data in memory to XOR, but chunk size limited to 1GB max, typically 8M, so okay
				// We'll read chunk into memory for simplicity but using streaming buffer for hash

				// Approach: read chunk via io.ReadFull into buffer sized by chunk (but chunk could be up to 1GB) - we need to avoid large allocation
				// Instead, we stream through hash and also collect data for encryption? For encryption we need data.
				// We can read chunk data into slice of size chunk.Size (max 1GB) - acceptable since chunk size limited to 1GB and default 8M
				// For true 100GB file, chunk iteration ensures memory efficiency (8M per chunk)

				// For memory efficiency with small chunks like 64KB, reading whole chunk is fine.
				// For large chunk 1GB, allocation could be heavy, but we have memory limit 4GB, so okay for parallel workers with 1GB each? That could OOM if parallel 4 and each allocates 1GB = 4GB.
				// Better to use streaming with hash and write encrypted data on the fly without holding full chunk.

				// We'll implement streaming: hash while copying to temp file, with encryption on the fly

				destChunkPath := filepath.Join(destDir, manifest.Chunks[chunkIdx].Path)
				tmpChunkPath := destChunkPath + ".tmp"

				if err := os.MkdirAll(filepath.Dir(destChunkPath), 0755); err != nil {
					lastErr = fmt.Errorf("mkdir chunk dir failed: %w", err)
					continue
				}

				tmpFile, err := os.Create(tmpChunkPath)
				if err != nil {
					lastErr = fmt.Errorf("create tmp chunk file failed: %w", err)
					continue
				}

				// Prepare hashers
				var shaH, md5H hash.Hash
				switch checksumAlgo {
				case "sha256":
					shaH = sha256.New()
				case "md5":
					md5H = md5.New()
				case "both":
					shaH = sha256.New()
					md5H = md5.New()
				}

				var writer io.Writer
				if shaH != nil && md5H != nil {
					writer = io.MultiWriter(shaH, md5H)
				} else if shaH != nil {
					writer = shaH
				} else if md5H != nil {
					writer = md5H
				}

				// If encryption, we need to encrypt data before writing to disk, but hash original data
				// So we hash original data, then encrypt for storage
				// Use TeeReader to hash while reading, then encrypt buffer before writing

				limited := io.LimitReader(srcFile, size)

				// We'll read through a buffer, hash original, encrypt, write encrypted
				var totalWritten int64
				var hashCopyErr error

				if encryptKey != "" {
					// Manual loop: read, hash, encrypt, write
					var offsetInChunk int64 = 0
					for {
						n, rerr := limited.Read(buf)
						if n > 0 {
							// Hash original data
							if writer != nil {
								_, _ = writer.Write(buf[:n])
							}
							// Encrypt
							enc := XorEncryptDecrypt(buf[:n], encryptKey) // Need offset-aware? For simplicity XOR cycling from 0 per chunk (not per file offset) - but spec says cycling per chunk is okay? We should use offsetInChunk for key cycling to be consistent across reads?
							// Our StreamingXor handles offset, but here we do simple per-chunk offset
							// Let's use offsetInChunk for key cycling to be correct across buffer boundaries
							keyBytes := []byte(encryptKey)
							for i := 0; i < n; i++ {
								enc[i] = buf[i] ^ keyBytes[int((offsetInChunk+int64(i))%int64(len(keyBytes)))]
							}
							written, werr := tmpFile.Write(enc[:n])
							totalWritten += int64(written)
							offsetInChunk += int64(n)
							if werr != nil {
								hashCopyErr = werr
								break
							}
						}
						if rerr != nil {
							if rerr == io.EOF {
								break
							}
							hashCopyErr = rerr
							break
						}
					}
				} else {
					// No encryption: copy with hashing via Tee
					if writer != nil {
						tee := io.TeeReader(limited, writer)
						written, err := io.CopyBuffer(tmpFile, tee, buf)
						totalWritten = written
						hashCopyErr = err
					} else {
						written, err := io.CopyBuffer(tmpFile, limited, buf)
						totalWritten = written
						hashCopyErr = err
					}
				}

				tmpFile.Close()

				if hashCopyErr != nil {
					os.Remove(tmpChunkPath)
					lastErr = fmt.Errorf("chunk %d copy failed: %w", chunkIdx, hashCopyErr)
					continue
				}

				if totalWritten != size {
					os.Remove(tmpChunkPath)
					lastErr = fmt.Errorf("chunk %d size mismatch: expected %d got %d", chunkIdx, size, totalWritten)
					continue
				}

				// Compute checksums
				var chunkSHA, chunkMD5 string
				if shaH != nil {
					chunkSHA = hex.EncodeToString(shaH.Sum(nil))
				}
				if md5H != nil {
					chunkMD5 = hex.EncodeToString(md5H.Sum(nil))
				}

				// Atomic rename
				if err := os.Rename(tmpChunkPath, destChunkPath); err != nil {
					os.Remove(tmpChunkPath)
					lastErr = fmt.Errorf("rename chunk %d failed: %w", chunkIdx, err)
					continue
				}

				// Verify written chunk after rename
				ok, err := VerifyChunkFile(destDir, ChunkInfo{
					Index:       chunkIdx,
					Size:        size,
					Checksum:    chunkSHA,
					ChecksumMD5: chunkMD5,
					Path:        manifest.Chunks[chunkIdx].Path,
				}, encryptKey, checksumAlgo)
				if err != nil {
					lastErr = fmt.Errorf("verify chunk %d failed: %w", chunkIdx, err)
					continue
				}
				if !ok {
					os.Remove(destChunkPath)
					lastErr = fmt.Errorf("chunk %d verification failed after write", chunkIdx)
					continue
				}

				// Success - update manifest thread-safely
				mu.Lock()
				manifest.Chunks[chunkIdx].Checksum = chunkSHA
				if checksumAlgo == "md5" {
					// For md5 only, store md5 in Checksum field as per spec (checksum is md5)
					// But also store in ChecksumMD5
					if chunkSHA == "" {
						manifest.Chunks[chunkIdx].Checksum = chunkMD5
					}
					manifest.Chunks[chunkIdx].ChecksumMD5 = chunkMD5
				} else if checksumAlgo == "both" {
					manifest.Chunks[chunkIdx].Checksum = chunkSHA
					manifest.Chunks[chunkIdx].ChecksumMD5 = chunkMD5
				} else {
					manifest.Chunks[chunkIdx].Checksum = chunkSHA
				}
				manifest.Chunks[chunkIdx].Uploaded = true
				// Save manifest
				saveErr := SaveManifest(manifest, manifestPath)
				mu.Unlock()

				if saveErr != nil {
					lastErr = fmt.Errorf("save manifest after chunk %d failed: %w", chunkIdx, saveErr)
					continue
				}

				fmt.Printf("Uploading chunk %d/%d (%d%%) worker %d\n", chunkIdx+1, manifest.TotalChunks, (chunkIdx+1)*100/manifest.TotalChunks, workerID)
				success = true
				break
			}

			if !success {
				setErr(fmt.Errorf("chunk %d failed after %d retries: %v", chunkIdx, retries, lastErr))
				// Continue to next job? Actually should stop?
				// For simplicity, continue to attempt other chunks but error will be returned after
			}
		}
	}

	// Enqueue jobs
	go func() {
		for i := int64(0); i < manifest.TotalChunks; i++ {
			mu.Lock()
			uploaded := manifest.Chunks[i].Uploaded
			mu.Unlock()
			if !uploaded {
				jobs <- job{index: i}
			}
		}
		close(jobs)
	}()

	// Start workers
	wg.Add(parallel)
	for w := 0; w < parallel; w++ {
		go worker(w)
	}

	wg.Wait()

	if firstErr != nil {
		return firstErr
	}

	// Assemble final file
	base := filepath.Base(sourceFile)
	finalPath := filepath.Join(destDir, base)

	if err := assembleFileFromManifest(manifest, finalPath); err != nil {
		return fmt.Errorf("assemble failed: %w", err)
	}

	fmt.Fprintf(os.Stderr, "Verifying final file checksum(s)...\n")
	switch checksumAlgo {
	case "sha256":
		finalChecksum, err := ComputeFileSHA256(finalPath)
		if err != nil {
			return fmt.Errorf("final checksum compute failed: %w", err)
		}
		if finalChecksum != fileChecksum {
			return fmt.Errorf("final checksum mismatch: expected %s got %s", fileChecksum, finalChecksum)
		}
	case "md5":
		finalChecksum, err := ComputeFileMD5(finalPath)
		if err != nil {
			return fmt.Errorf("final checksum compute failed: %w", err)
		}
		if finalChecksum != fileChecksum {
			return fmt.Errorf("final checksum mismatch: expected %s got %s", fileChecksum, finalChecksum)
		}
	case "both":
		finalSHA, finalMD5, err := ComputeFileBoth(finalPath)
		if err != nil {
			return fmt.Errorf("final checksum compute failed: %w", err)
		}
		if finalSHA != fileChecksum {
			return fmt.Errorf("final checksum mismatch: expected %s got %s", fileChecksum, finalSHA)
		}
		if finalMD5 != fileChecksumMD5 {
			return fmt.Errorf("final md5 mismatch: expected %s got %s", fileChecksumMD5, finalMD5)
		}
	}

	fmt.Printf("UPLOAD COMPLETE: %s Size: %d Checksum: %s Chunks: %d Parallel: %d ChecksumAlgo: %s\n", finalPath, sourceSize, fileChecksum, manifest.TotalChunks, parallel, checksumAlgo)

	return nil
}

func assembleFileFromManifest(manifest *Manifest, outputPath string) error {
	outFile, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("create output failed: %w", err)
	}
	defer outFile.Close()

	buf := make([]byte, 1024*1024)

	for _, chunk := range manifest.Chunks {
		chunkPath := filepath.Join(manifest.DestDir, chunk.Path)

		f, err := os.Open(chunkPath)
		if err != nil {
			return fmt.Errorf("open chunk %d failed: %w", chunk.Index, err)
		}

		if manifest.EncryptKey != "" {
			// Decrypt while copying
			_, err = StreamingXor(f, outFile, manifest.EncryptKey, buf)
			if err != nil {
				f.Close()
				return fmt.Errorf("decrypt and copy chunk %d failed: %w", chunk.Index, err)
			}
		} else {
			if _, err := io.CopyBuffer(outFile, f, buf); err != nil {
				f.Close()
				return fmt.Errorf("copy chunk %d failed: %w", chunk.Index, err)
			}
		}
		f.Close()
	}

	return nil
}

func AssembleFromManifest(manifestPath string, outputPath string) error {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return fmt.Errorf("load manifest failed: %w", err)
	}

	if outputPath == "" {
		base := filepath.Base(manifest.SourceFile)
		outputPath = filepath.Join(manifest.DestDir, base)
	}

	if err := os.MkdirAll(filepath.Dir(outputPath), 0755); err != nil {
		return fmt.Errorf("mkdir output dir failed: %w", err)
	}

	if err := assembleFileFromManifest(manifest, outputPath); err != nil {
		return err
	}

	if manifest.FileChecksum != "" {
		fmt.Fprintf(os.Stderr, "Verifying assembled file...\n")
		switch manifest.ChecksumAlgo {
		case "sha256":
			actual, err := ComputeFileSHA256(outputPath)
			if err != nil {
				return fmt.Errorf("checksum failed: %w", err)
			}
			if actual != manifest.FileChecksum {
				return fmt.Errorf("final checksum mismatch: expected %s got %s", manifest.FileChecksum, actual)
			}
		case "md5":
			actual, err := ComputeFileMD5(outputPath)
			if err != nil {
				return fmt.Errorf("checksum failed: %w", err)
			}
			if actual != manifest.FileChecksum {
				return fmt.Errorf("final checksum mismatch: expected %s got %s", manifest.FileChecksum, actual)
			}
		case "both":
			actualSHA, actualMD5, err := ComputeFileBoth(outputPath)
			if err != nil {
				return fmt.Errorf("checksum failed: %w", err)
			}
			if actualSHA != manifest.FileChecksum {
				return fmt.Errorf("final checksum mismatch: expected %s got %s", manifest.FileChecksum, actualSHA)
			}
			if manifest.FileChecksumMD5 != "" && actualMD5 != manifest.FileChecksumMD5 {
				return fmt.Errorf("final md5 mismatch: expected %s got %s", manifest.FileChecksumMD5, actualMD5)
			}
		default:
			actual, err := ComputeFileSHA256(outputPath)
			if err != nil {
				return fmt.Errorf("checksum failed: %w", err)
			}
			if actual != manifest.FileChecksum {
				return fmt.Errorf("final checksum mismatch: expected %s got %s", manifest.FileChecksum, actual)
			}
		}
	}

	fmt.Printf("ASSEMBLE COMPLETE: %s\n", outputPath)
	return nil
}
