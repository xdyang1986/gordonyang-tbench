package main

import "fmt"

func UploadFile(sourceFile string, destDir string, chunkSize int64, manifestPath string, parallel int, retries int, checksumAlgo string, encryptKey string) error {
	return fmt.Errorf("not implemented")
}

func AssembleFromManifest(manifestPath string, outputPath string) error {
	return fmt.Errorf("not implemented")
}
