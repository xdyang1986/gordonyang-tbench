package main

import (
	"encoding/json"
	"fmt"
	"os"
)

func printHelp() {
	help := `Large File Upload Processor - YouTube-like video upload handler (HARD MODE)

Usage:
  go run . <command> [options]

Commands:
  validate  Validate video file format (magic bytes + extension)
    --file <path>         Path to video file

  info      Show file info as JSON
    --file <path>         Path to video file
    --chunk-size <size>   Optional chunk size (default 8M)
    --checksum <algo>     sha256|md5|both (default sha256)

  upload    Chunked resumable upload with parallel workers, retries, encryption
    --source <path>       Source video file
    --dest <dir>          Destination directory (simulated remote storage)
    --chunk-size <size>   Chunk size e.g. 8M, 4M, 1G (default 8M)
    --manifest <path>     Manifest JSON path (default <dest>/<filename>.manifest.json)
    --parallel <N>        Parallel workers 1-32 (default 4)
    --retries <N>         Retries 0-10 with exponential backoff (default 3)
    --checksum <algo>     sha256|md5|both (default sha256)
    --encrypt-key <key>   XOR encryption key (optional)

  assemble  Manually assemble file from manifest chunks
    --manifest <path>     Manifest JSON path
    --output <path>       Output file path (default <dest>/<filename> from manifest)

  help      Show this help
`
	fmt.Print(help)
}

func main() {
	if len(os.Args) < 2 {
		printHelp()
		os.Exit(2)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	switch cmd {
	case "validate":
		filePath := ""
		for i := 0; i < len(args); i++ {
			if args[i] == "--file" && i+1 < len(args) {
				filePath = args[i+1]
				i++
			} else if args[i] == "-h" || args[i] == "--help" {
				fmt.Println("Usage: validate --file <path>")
				os.Exit(0)
			}
		}
		if filePath == "" {
			fmt.Fprintln(os.Stderr, "ERROR: --file required")
			os.Exit(2)
		}
		format, err := ValidateVideoFile(filePath)
		if err != nil {
			fmt.Printf("INVALID: %s\n", err.Error())
			os.Exit(1)
		}
		fmt.Printf("VALID: %s\n", format)
		os.Exit(0)

	case "info":
		filePath := ""
		chunkSizeStr := "8M"
		checksumAlgo := "sha256"
		for i := 0; i < len(args); i++ {
			if args[i] == "--file" && i+1 < len(args) {
				filePath = args[i+1]
				i++
			} else if args[i] == "--chunk-size" && i+1 < len(args) {
				chunkSizeStr = args[i+1]
				i++
			} else if args[i] == "--checksum" && i+1 < len(args) {
				checksumAlgo = args[i+1]
				i++
			} else if args[i] == "-h" || args[i] == "--help" {
				fmt.Println("Usage: info --file <path> [--chunk-size <size>] [--checksum <algo>]")
				os.Exit(0)
			}
		}
		if filePath == "" {
			fmt.Fprintln(os.Stderr, "ERROR: --file required")
			os.Exit(2)
		}
		chunkSize, err := ParseChunkSize(chunkSizeStr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: invalid chunk size: %s\n", err.Error())
			os.Exit(2)
		}
		if _, err := ParseChecksumAlgo(checksumAlgo); err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(2)
		}
		info, err := GetFileInfo(filePath, chunkSize, checksumAlgo)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(1)
		}
		b, _ := json.MarshalIndent(info, "", "  ")
		fmt.Println(string(b))
		os.Exit(0)

	case "upload":
		var source, dest, chunkSizeStr, manifestPath, encryptKey, checksumAlgo, parallelStr, retriesStr string
		chunkSizeStr = "8M"
		parallelStr = "4"
		retriesStr = "3"
		checksumAlgo = "sha256"
		for i := 0; i < len(args); i++ {
			switch args[i] {
			case "--source":
				if i+1 < len(args) {
					source = args[i+1]
					i++
				}
			case "--dest":
				if i+1 < len(args) {
					dest = args[i+1]
					i++
				}
			case "--chunk-size":
				if i+1 < len(args) {
					chunkSizeStr = args[i+1]
					i++
				}
			case "--manifest":
				if i+1 < len(args) {
					manifestPath = args[i+1]
					i++
				}
			case "--parallel":
				if i+1 < len(args) {
					parallelStr = args[i+1]
					i++
				}
			case "--retries":
				if i+1 < len(args) {
					retriesStr = args[i+1]
					i++
				}
			case "--checksum":
				if i+1 < len(args) {
					checksumAlgo = args[i+1]
					i++
				}
			case "--encrypt-key":
				if i+1 < len(args) {
					encryptKey = args[i+1]
					i++
				}
			case "-h", "--help":
				fmt.Println("Usage: upload --source <path> --dest <dir> [--chunk-size <size>] [--manifest <path>] [--parallel <N>] [--retries <N>] [--checksum <algo>] [--encrypt-key <key>]")
				os.Exit(0)
			}
		}
		if source == "" || dest == "" {
			fmt.Fprintln(os.Stderr, "ERROR: --source and --dest required")
			os.Exit(2)
		}
		chunkSize, err := ParseChunkSize(chunkSizeStr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: invalid chunk size: %s\n", err.Error())
			os.Exit(2)
		}
		parallel, err := ParseParallel(parallelStr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(2)
		}
		retries, err := ParseRetries(retriesStr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(2)
		}
		if _, err := ParseChecksumAlgo(checksumAlgo); err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(2)
		}
		if manifestPath == "" {
			base := source
			for j := len(source) - 1; j >= 0; j-- {
				if source[j] == '/' {
					base = source[j+1:]
					break
				}
			}
			manifestPath = fmt.Sprintf("%s/%s.manifest.json", dest, base)
		}
		err = UploadFile(source, dest, chunkSize, manifestPath, parallel, retries, checksumAlgo, encryptKey)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(1)
		}
		os.Exit(0)

	case "assemble":
		var manifestPath, outputPath string
		for i := 0; i < len(args); i++ {
			switch args[i] {
			case "--manifest":
				if i+1 < len(args) {
					manifestPath = args[i+1]
					i++
				}
			case "--output":
				if i+1 < len(args) {
					outputPath = args[i+1]
					i++
				}
			case "-h", "--help":
				fmt.Println("Usage: assemble --manifest <path> [--output <path>]")
				os.Exit(0)
			}
		}
		if manifestPath == "" {
			fmt.Fprintln(os.Stderr, "ERROR: --manifest required")
			os.Exit(2)
		}
		err := AssembleFromManifest(manifestPath, outputPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %s\n", err.Error())
			os.Exit(1)
		}
		os.Exit(0)

	case "help", "-h", "--help":
		printHelp()
		os.Exit(0)
	default:
		fmt.Fprintf(os.Stderr, "ERROR: unknown command %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
