package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// UploadFile performs chunked resumable upload
func UploadFile(sourceFile string, destDir string, chunkSize int64, manifestPath string) error {
	// TODO: implement full upload logic
	// Steps:
	// 1. Validate video file format (use ValidateVideoFile)
	// 2. Stat source file size (int64)
	// 3. Compute file checksum streaming (ComputeFileSHA256) - or do after manifest creation? For resume, checksum needed to verify source unchanged. Compute first.
	// 4. Try load existing manifest if exists
	//    - If corrupted JSON, warn to stderr "WARN: corrupted manifest, starting fresh" and create new
	//    - If manifest exists and source_size != current size or source_file != current source file path? Check size mismatch -> error "source file changed since manifest creation (size mismatch)"
	//    - Also check file checksum? If existing manifest has file_checksum and it differs from current computed, source changed
	//    - Otherwise resume: for each chunk marked uploaded, verify chunk file on disk (VerifyChunkFile). If missing or corrupted, mark not uploaded.
	// 5. If no manifest or fresh start: create new manifest via CreateManifest, compute file checksum
	// 6. Ensure dest/chunks exists (os.MkdirAll)
	// 7. Upload loop: for each chunk not uploaded:
	//    - Read chunk from source using Seek + limited read (streaming buffer)
	//    - Compute chunk checksum
	//    - Write chunk atomically to dest/chunks/chunk_%06d (temp file + rename)
	//    - Verify written chunk
	//    - Update manifest chunk: checksum, uploaded=true, save manifest atomically
	//    - Print progress: "Uploading chunk X/Y (Z%)"
	// 8. After all chunks: assemble file to dest/<basename> streaming via AssembleFromManifest or manual concat
	// 9. Verify assembled file checksum matches source checksum, else error "final checksum mismatch"
	// 10. Print success: "UPLOAD COMPLETE: <dest>/<filename> Size: <bytes> Checksum: <sha256> Chunks: <total>"

	// Validation
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

	// Compute source checksum (streaming) - needed for manifest and final verification
	fmt.Fprintf(os.Stderr, "Computing source file checksum (streaming)...\n")
	fileChecksum, err := ComputeFileSHA256(sourceFile)
	if err != nil {
		return fmt.Errorf("checksum failed: %w", err)
	}

	var manifest *Manifest

	// Try load existing manifest for resume
	if _, err := os.Stat(manifestPath); err == nil {
		loaded, err := LoadManifest(manifestPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "WARN: corrupted manifest, starting fresh: %s\n", err.Error())
			// Continue to create new manifest
		} else {
			// Validate source file size matches
			if loaded.SourceSize != sourceSize {
				return fmt.Errorf("source file changed since manifest creation (size mismatch): manifest %d vs current %d", loaded.SourceSize, sourceSize)
			}
			// Optionally check file path
			// If chunk size differs, error? Or allow resume with same chunk size only. For simplicity, require same chunk size.
			if loaded.ChunkSize != chunkSize {
				fmt.Fprintf(os.Stderr, "WARN: chunk size changed from %d to %d, using manifest's chunk size\n", loaded.ChunkSize, chunkSize)
				chunkSize = loaded.ChunkSize
			}

			// Verify existing chunks
			fmt.Fprintf(os.Stderr, "Resuming upload session %s, verifying existing chunks...\n", loaded.SessionID)
			for i := range loaded.Chunks {
				if loaded.Chunks[i].Uploaded {
					ok, _ := VerifyChunkFile(loaded.DestDir, loaded.Chunks[i])
					if !ok {
						fmt.Fprintf(os.Stderr, "Chunk %d corrupted or missing, will re-upload\n", loaded.Chunks[i].Index)
						loaded.Chunks[i].Uploaded = false
					}
				}
			}
			manifest = loaded
		}
	}

	if manifest == nil {
		manifest, err = CreateManifest(sourceFile, sourceSize, chunkSize, destDir, fileChecksum)
		if err != nil {
			return fmt.Errorf("manifest creation failed: %w", err)
		}
	}

	// Ensure dest and chunks dir exist
	chunksDir := filepath.Join(destDir, "chunks")
	if err := os.MkdirAll(chunksDir, 0755); err != nil {
		return fmt.Errorf("mkdir dest failed: %w", err)
	}

	// Save initial manifest
	if err := SaveManifest(manifest, manifestPath); err != nil {
		return fmt.Errorf("save manifest failed: %w", err)
	}

	// Open source file for reading chunks (streaming)
	srcFile, err := os.Open(sourceFile)
	if err != nil {
		return fmt.Errorf("open source failed: %w", err)
	}
	defer srcFile.Close()

	// TODO: implement chunk upload loop with streaming, atomic writes, progress

	for i := int64(0); i < manifest.TotalChunks; i++ {
		chunk := &manifest.Chunks[i]
		if chunk.Uploaded {
			continue // Already uploaded and verified
		}

		fmt.Printf("Uploading chunk %d/%d (%d%%)\n", chunk.Index+1, manifest.TotalChunks, (chunk.Index+1)*100/manifest.TotalChunks)

		// TODO: Read chunk via Seek and streaming
		// Seek to offset
		if _, err := srcFile.Seek(chunk.Offset, io.SeekStart); err != nil {
			return fmt.Errorf("seek failed for chunk %d: %w", chunk.Index, err)
		}

		// Read chunk data - should use buffered reading, not loading huge
		// For simplicity of this skeleton, reading chunk into memory (but chunk size is limited to 1GB max, so okay for chunk)
		// However must NOT load whole file, only chunk
		// For true 100GB file, chunk iteration ensures memory efficiency

		// Allocate buffer for chunk size (chunk size limited to 1GB, default 8M, so okay)
		// Use io.LimitReader and hash while reading? Let's read and compute hash simultaneously
		limitedReader := io.LimitReader(srcFile, chunk.Size)

		// Create hash while reading to temp chunk file
		// Atomic write: write to temp file then rename

		tmpChunkPath := filepath.Join(destDir, chunk.Path+".tmp")
		// Ensure parent dir for chunk path exists (chunks dir already)
		// Actually chunk.Path includes "chunks/chunk_..."

		destChunkPath := filepath.Join(destDir, chunk.Path)

		// Ensure chunk path dir exists
		if err := os.MkdirAll(filepath.Dir(destChunkPath), 0755); err != nil {
			return fmt.Errorf("mkdir chunk dir failed: %w", err)
		}

		tmpFile, err := os.Create(tmpChunkPath)
		if err != nil {
			return fmt.Errorf("create tmp chunk file failed: %w", err)
		}

		// Hash while copying? Let's use io.TeeReader or copy + hash
		// Simpler: hash reader data and write to file

		// We need to compute chunk checksum
		// Approach: read chunk into buffer, compute hash, write
		// For memory efficiency, stream with buffer

		// TODO: streaming copy with hash
		// Current placeholder uses CopyBuffer with hash? We need to hash data while writing to tmp file

		// Placeholder - implement correct streaming
		hasher := newHasher() // custom helper? We need to implement
		_ = hasher
		_ = limitedReader
		_ = tmpFile
		_ = tmpChunkPath
		_ = destChunkPath

		// This skeleton intentionally incomplete - you must implement!
		// Steps for each chunk:
		// 1. Read chunk data from sourceFile at offset, limited to chunk.Size, using a 1MB buffer
		// 2. Compute SHA256 of chunk data
		// 3. Write chunk data atomically to dest
		// 4. Update manifest

		// For now, fail to indicate TODO
		tmpFile.Close()
		os.Remove(tmpChunkPath)
		return fmt.Errorf("TODO: implement chunk upload logic for chunk %d", chunk.Index)
	}

	// Assemble final file
	base := filepath.Base(sourceFile)
	finalPath := filepath.Join(destDir, base)

	if err := assembleFileFromManifest(manifest, finalPath); err != nil {
		return fmt.Errorf("assemble failed: %w", err)
	}

	// Verify final checksum
	fmt.Fprintf(os.Stderr, "Verifying final file checksum...\n")
	finalChecksum, err := ComputeFileSHA256(finalPath)
	if err != nil {
		return fmt.Errorf("final checksum compute failed: %w", err)
	}
	if finalChecksum != fileChecksum {
		return fmt.Errorf("final checksum mismatch: expected %s got %s", fileChecksum, finalChecksum)
	}

	fmt.Printf("UPLOAD COMPLETE: %s Size: %d Checksum: %s Chunks: %d\n", finalPath, sourceSize, fileChecksum, manifest.TotalChunks)

	return nil
}

// Helper to create hasher (placeholder)
func newHasher() io.Writer {
	// TODO: implement
	return nil
}

func assembleFileFromManifest(manifest *Manifest, outputPath string) error {
	// TODO: implement streaming concatenation
	// - Create output file
	// - For each chunk in order, open chunk file, io.Copy to output
	// - Should not load all chunks into memory
	// - Use 1MB buffer

	outFile, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("create output failed: %w", err)
	}
	defer outFile.Close()

	buf := make([]byte, 1024*1024) // 1MB buffer

	for _, chunk := range manifest.Chunks {
		chunkPath := filepath.Join(manifest.DestDir, chunk.Path)
		// Alternative: if manifest.DestDir != dest of assembly, need to handle
		// But we use manifest DestDir + chunk path

		f, err := os.Open(chunkPath)
		if err != nil {
			return fmt.Errorf("open chunk %d failed: %w", chunk.Index, err)
		}

		if _, err := io.CopyBuffer(outFile, f, buf); err != nil {
			f.Close()
			return fmt.Errorf("copy chunk %d failed: %w", chunk.Index, err)
		}
		f.Close()
	}

	return nil
}

// AssembleFromManifest public API for manual assembly
func AssembleFromManifest(manifestPath string, outputPath string) error {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return fmt.Errorf("load manifest failed: %w", err)
	}

	if outputPath == "" {
		// Default to dest_dir + base name from source
		base := filepath.Base(manifest.SourceFile)
		outputPath = filepath.Join(manifest.DestDir, base)
	}

	// Ensure output dir exists
	if err := os.MkdirAll(filepath.Dir(outputPath), 0755); err != nil {
		return fmt.Errorf("mkdir output dir failed: %w", err)
	}

	if err := assembleFileFromManifest(manifest, outputPath); err != nil {
		return err
	}

	// Verify checksum if available
	if manifest.FileChecksum != "" {
		fmt.Fprintf(os.Stderr, "Verifying assembled file...\n")
		actual, err := ComputeFileSHA256(outputPath)
		if err != nil {
			return fmt.Errorf("checksum failed: %w", err)
		}
		if actual != manifest.FileChecksum {
			return fmt.Errorf("final checksum mismatch: expected %s got %s", manifest.FileChecksum, actual)
		}
	}

	fmt.Printf("ASSEMBLE COMPLETE: %s\n", outputPath)
	return nil
}
