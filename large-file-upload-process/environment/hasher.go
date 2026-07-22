package main

import "fmt"

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
