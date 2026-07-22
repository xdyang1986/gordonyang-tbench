package main

import (
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"os"
)

func ComputeFileSHA256(filePath string) (string, error) {
	return computeFileHash(filePath, "sha256")
}

func ComputeFileMD5(filePath string) (string, error) {
	return computeFileHash(filePath, "md5")
}

func ComputeFileBoth(filePath string) (string, string, error) {
	sha, md5sum, err := computeFileBothHashes(filePath)
	return sha, md5sum, err
}

func computeFileHash(filePath string, algo string) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	var h hash.Hash
	switch algo {
	case "sha256":
		h = sha256.New()
	case "md5":
		h = md5.New()
	default:
		return "", fmt.Errorf("invalid checksum algo: %s", algo)
	}

	buf := make([]byte, 1024*1024)
	if _, err := io.CopyBuffer(h, f, buf); err != nil {
		return "", fmt.Errorf("hash copy failed: %w", err)
	}

	return hex.EncodeToString(h.Sum(nil)), nil
}

func computeFileBothHashes(filePath string) (string, string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	shaH := sha256.New()
	md5H := md5.New()

	// Use TeeReader to compute both in one pass? For simplicity, use MultiWriter
	// But io.Copy with MultiWriter
	mw := io.MultiWriter(shaH, md5H)
	buf := make([]byte, 1024*1024)
	if _, err := io.CopyBuffer(mw, f, buf); err != nil {
		return "", "", fmt.Errorf("hash copy failed: %w", err)
	}

	return hex.EncodeToString(shaH.Sum(nil)), hex.EncodeToString(md5H.Sum(nil)), nil
}

func ComputeChunkSHA256(filePath string, offset int64, size int64) (string, error) {
	return computeChunkHash(filePath, offset, size, "sha256")
}

func ComputeChunkMD5(filePath string, offset int64, size int64) (string, error) {
	return computeChunkHash(filePath, offset, size, "md5")
}

func ComputeChunkBoth(filePath string, offset int64, size int64) (string, string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	_, err = f.Seek(offset, io.SeekStart)
	if err != nil {
		return "", "", fmt.Errorf("seek failed: %w", err)
	}

	shaH := sha256.New()
	md5H := md5.New()
	mw := io.MultiWriter(shaH, md5H)
	limited := io.LimitReader(f, size)
	buf := make([]byte, 1024*1024)
	if _, err := io.CopyBuffer(mw, limited, buf); err != nil {
		return "", "", fmt.Errorf("chunk hash copy failed: %w", err)
	}

	return hex.EncodeToString(shaH.Sum(nil)), hex.EncodeToString(md5H.Sum(nil)), nil
}

func computeChunkHash(filePath string, offset int64, size int64, algo string) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	_, err = f.Seek(offset, io.SeekStart)
	if err != nil {
		return "", fmt.Errorf("seek failed: %w", err)
	}

	var h hash.Hash
	switch algo {
	case "sha256":
		h = sha256.New()
	case "md5":
		h = md5.New()
	default:
		return "", fmt.Errorf("invalid checksum algo: %s", algo)
	}

	limited := io.LimitReader(f, size)
	buf := make([]byte, 1024*1024)
	if _, err := io.CopyBuffer(h, limited, buf); err != nil {
		return "", fmt.Errorf("chunk hash copy failed: %w", err)
	}

	return hex.EncodeToString(h.Sum(nil)), nil
}

func ComputeBytesSHA256(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func ComputeBytesMD5(data []byte) string {
	h := md5.Sum(data)
	return hex.EncodeToString(h[:])
}

func ComputeBytesBoth(data []byte) (string, string) {
	sha := sha256.Sum256(data)
	md5sum := md5.Sum(data)
	return hex.EncodeToString(sha[:]), hex.EncodeToString(md5sum[:])
}

func VerifyFileChecksum(filePath string, expected string) (bool, error) {
	actual, err := ComputeFileSHA256(filePath)
	if err != nil {
		return false, err
	}
	return actual == expected, nil
}

// XOR encrypt/decrypt (same operation)
func XorEncryptDecrypt(data []byte, key string) []byte {
	if key == "" {
		return data
	}
	keyBytes := []byte(key)
	result := make([]byte, len(data))
	for i := 0; i < len(data); i++ {
		result[i] = data[i] ^ keyBytes[i%len(keyBytes)]
	}
	return result
}

// Streaming XOR: reads from reader, writes XORed to writer, using key
func StreamingXor(reader io.Reader, writer io.Writer, key string, buf []byte) (int64, error) {
	if key == "" {
		return io.CopyBuffer(writer, reader, buf)
	}
	keyBytes := []byte(key)
	keyLen := len(keyBytes)
	var total int64
	var offset int64 = 0
	for {
		n, err := reader.Read(buf)
		if n > 0 {
			// XOR in place
			for i := 0; i < n; i++ {
				buf[i] = buf[i] ^ keyBytes[(int(offset)+i)%keyLen]
			}
			written, werr := writer.Write(buf[:n])
			total += int64(written)
			offset += int64(n)
			if werr != nil {
				return total, werr
			}
		}
		if err != nil {
			if err == io.EOF {
				break
			}
			return total, err
		}
	}
	return total, nil
}
