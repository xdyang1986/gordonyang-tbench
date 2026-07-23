package main

import "fmt"

// TODO: Implement streaming hashes with 1MB buffer, no ReadFile
// ComputeFileSHA256: open file, sha256.New(), io.CopyBuffer with 1MB buffer, hex encode
// ComputeFileMD5: same with md5.New()
// ComputeFileBoth: use io.MultiWriter(sha256, md5) to compute both in one pass
// ComputeChunk: Seek to offset, LimitReader(size), same hashing
// XorEncryptDecrypt: XOR with key cycling: result[i] = data[i] ^ key[i%len(key)]
// StreamingXor: for assembly, read chunk file, decrypt on fly with offset-aware key cycling

func ComputeFileSHA256(filePath string) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeFileMD5(filePath string) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeFileBoth(filePath string) (string, string, error) {
	return "", "", fmt.Errorf("not implemented")
}

func ComputeChunkSHA256(filePath string, offset int64, size int64) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeChunkMD5(filePath string, offset int64, size int64) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeChunkBoth(filePath string, offset int64, size int64) (string, string, error) {
	return "", "", fmt.Errorf("not implemented")
}

func ComputeBytesSHA256(data []byte) string {
	return ""
}

func ComputeBytesMD5(data []byte) string {
	return ""
}

func XorEncryptDecrypt(data []byte, key string) []byte {
	return nil
}
