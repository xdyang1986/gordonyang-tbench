package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

func UploadFile(sourceFile string, destDir string, chunkSize int64, manifestPath string) error {
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

	fmt.Fprintf(os.Stderr, "Computing source file checksum (streaming)...\n")
	fileChecksum, err := ComputeFileSHA256(sourceFile)
	if err != nil {
		return fmt.Errorf("checksum failed: %w", err)
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
			if loaded.ChunkSize != chunkSize {
				fmt.Fprintf(os.Stderr, "WARN: chunk size changed from %d to %d, using manifest's chunk size\n", loaded.ChunkSize, chunkSize)
				chunkSize = loaded.ChunkSize
			}

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
	} else {
		// Update file checksum in case source didn't change but manifest had old checksum? Keep computed.
		manifest.FileChecksum = fileChecksum
		// Ensure DestDir is current destDir? Use manifest's DestDir if exists else new
		if manifest.DestDir == "" {
			manifest.DestDir = destDir
		}
	}

	chunksDir := filepath.Join(destDir, "chunks")
	if err := os.MkdirAll(chunksDir, 0755); err != nil {
		return fmt.Errorf("mkdir dest failed: %w", err)
	}

	if err := SaveManifest(manifest, manifestPath); err != nil {
		return fmt.Errorf("save manifest failed: %w", err)
	}

	srcFile, err := os.Open(sourceFile)
	if err != nil {
		return fmt.Errorf("open source failed: %w", err)
	}
	defer srcFile.Close()

	// Reusable 1MB buffer for streaming
	buf := make([]byte, 1024*1024)

	for i := int64(0); i < manifest.TotalChunks; i++ {
		chunk := &manifest.Chunks[i]
		if chunk.Uploaded {
			continue
		}

		fmt.Printf("Uploading chunk %d/%d (%d%%)\n", chunk.Index+1, manifest.TotalChunks, (chunk.Index+1)*100/manifest.TotalChunks)

		if _, err := srcFile.Seek(chunk.Offset, io.SeekStart); err != nil {
			return fmt.Errorf("seek failed for chunk %d: %w", chunk.Index, err)
		}

		destChunkPath := filepath.Join(destDir, chunk.Path)
		tmpChunkPath := destChunkPath + ".tmp"

		if err := os.MkdirAll(filepath.Dir(destChunkPath), 0755); err != nil {
			return fmt.Errorf("mkdir chunk dir failed: %w", err)
		}

		tmpFile, err := os.Create(tmpChunkPath)
		if err != nil {
			return fmt.Errorf("create tmp chunk file failed: %w", err)
		}

		hasher := sha256.New()
		limited := io.LimitReader(srcFile, chunk.Size)

		// TeeReader to hash while writing to temp file
		tee := io.TeeReader(limited, hasher)

		written, err := io.CopyBuffer(tmpFile, tee, buf)
		if err != nil {
			tmpFile.Close()
			os.Remove(tmpChunkPath)
			return fmt.Errorf("chunk %d copy failed: %w", chunk.Index, err)
		}
		tmpFile.Close()

		if written != chunk.Size {
			os.Remove(tmpChunkPath)
			return fmt.Errorf("chunk %d size mismatch: expected %d got %d", chunk.Index, chunk.Size, written)
		}

		chunkChecksum := hex.EncodeToString(hasher.Sum(nil))

		// Atomic rename
		if err := os.Rename(tmpChunkPath, destChunkPath); err != nil {
			os.Remove(tmpChunkPath)
			return fmt.Errorf("rename chunk %d failed: %w", chunk.Index, err)
		}

		// Verify written chunk
		ok, err := VerifyChunkFileDirect(destChunkPath, chunk.Size, chunkChecksum)
		if err != nil {
			return fmt.Errorf("verify chunk %d failed: %w", chunk.Index, err)
		}
		if !ok {
			os.Remove(destChunkPath)
			return fmt.Errorf("chunk %d verification failed after write", chunk.Index)
		}

		chunk.Checksum = chunkChecksum
		chunk.Uploaded = true

		if err := SaveManifest(manifest, manifestPath); err != nil {
			return fmt.Errorf("save manifest after chunk %d failed: %w", chunk.Index, err)
		}
	}

	base := filepath.Base(sourceFile)
	finalPath := filepath.Join(destDir, base)

	if err := assembleFileFromManifest(manifest, finalPath); err != nil {
		return fmt.Errorf("assemble failed: %w", err)
	}

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

func VerifyChunkFileDirect(chunkPath string, expectedSize int64, expectedChecksum string) (bool, error) {
	info, err := os.Stat(chunkPath)
	if err != nil {
		return false, err
	}
	if info.Size() != expectedSize {
		return false, nil
	}
	actual, err := ComputeFileSHA256(chunkPath)
	if err != nil {
		return false, err
	}
	return actual == expectedChecksum, nil
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

		if _, err := io.CopyBuffer(outFile, f, buf); err != nil {
			f.Close()
			return fmt.Errorf("copy chunk %d failed: %w", chunk.Index, err)
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
