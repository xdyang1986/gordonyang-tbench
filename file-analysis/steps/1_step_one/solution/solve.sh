#!/bin/bash
set -euo pipefail

cd /app 2>/dev/null || cd /testbed 2>/dev/null

cat > go.mod << 'GOMOD'
module file-analyzer

go 1.22
GOMOD

cat > main.go << 'GOMAIN'
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type Result struct {
	File     string `json:"file"`
	Category string `json:"category"`
}

func printHelp() {
	fmt.Println(`Usage: file-analyzer --dir <path> --output <path> [options]
File relevance analyzer - classifies files as business-critical, pii, or non-essential

Flags:
  --dir string     Directory to scan recursively
  --output string  Output JSON file path
  --help, -h       Show help
  help             Show help

Examples:
  file-analyzer --dir /tmp/data --output /tmp/out.json
  file-analyzer --help`)
}

const (
	bufSize = 1 * 1024 * 1024
	overlap = 256
	maxLineFrag = 8192
)

func containsDigit(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= '0' && s[i] <= '9' {
			return true
		}
	}
	return false
}

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		printHelp()
		os.Exit(0)
	}
	for _, a := range args {
		if a == "--help" || a == "-h" || a == "help" || a == "--help=true" || a == "-h=true" {
			printHelp()
			os.Exit(0)
		}
	}
	known := map[string]bool{"--dir": true, "--output": true, "--help": true, "-h": true}
	for _, a := range args {
		if strings.HasPrefix(a, "-") {
			key := a
			if idx := strings.Index(a, "="); idx != -1 {
				key = a[:idx]
			}
			if !known[key] {
				if strings.HasPrefix(a, "--dir=") || strings.HasPrefix(a, "--output=") {
					continue
				}
				fmt.Fprintf(os.Stderr, "unknown flag %s\n", a)
				os.Exit(2)
			}
		}
	}
	fset := flag.NewFlagSet("file-analyzer", flag.ContinueOnError)
	dirFlag := fset.String("dir", "", "Directory to scan")
	outputFlag := fset.String("output", "", "Output JSON file")
	fset.SetOutput(os.Stderr)
	if err := fset.Parse(args); err != nil {
		os.Exit(2)
	}
	if *dirFlag == "" || *outputFlag == "" {
		fmt.Fprintln(os.Stderr, "missing required --dir and --output")
		os.Exit(2)
	}
	info, err := os.Stat(*dirFlag)
	if err != nil {
		fmt.Fprintf(os.Stderr, "dir error: %v\n", err)
		os.Exit(2)
	}
	if !info.IsDir() {
		fmt.Fprintln(os.Stderr, "dir is not a directory")
		os.Exit(2)
	}
	outDir := filepath.Dir(*outputFlag)
	if err := os.MkdirAll(outDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "failed to create output dir: %v\n", err)
		os.Exit(2)
	}

	ssnRe := regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`)
	emailRe := regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`)
	phoneRe1 := regexp.MustCompile(`\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`)
	phoneRe2 := regexp.MustCompile(`\(\d{3}\)\s*\d{3}[-.]\d{4}`)
	ccRe := regexp.MustCompile(`\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`)
	logLineRe := regexp.MustCompile(`(?i)(^\d{4}-\d{2}-\d{2}|^\[?\d{2}:\d{2}:\d{2}|\b(INFO|DEBUG|WARN|ERROR|TRACE)\b)`)

	keywords := []string{
		"confidential", "proprietary", "trade secret", "financial", "revenue", "budget",
		"forecast", "strategic", "merger", "acquisition", "contract", "nda",
		"intellectual property", "earnings", "profit", "balance sheet", "board meeting", "shareholder",
	}

	var filePaths []string
	filepath.WalkDir(*dirFlag, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		if d.IsDir() {
			return nil
		}
		filePaths = append(filePaths, path)
		return nil
	})

	results := make([]Result, 0, len(filePaths))

	for _, path := range filePaths {
		f, err := os.Open(path)
		if err != nil {
			results = append(results, Result{File: path, Category: "non-essential"})
			continue
		}

		hasNonWhitespace := false
		hasNull := false
		isPII := false
		isBiz := false
		totalLines := 0
		matchedLines := 0
		lineRemainder := ""
		patternCarry := ""
		buf := make([]byte, bufSize)

		for {
			n, err := f.Read(buf)
			if n > 0 {
				chunk := string(buf[:n])
				if !hasNonWhitespace {
					for _, r := range chunk {
						if r != ' ' && r != '\n' && r != '\r' && r != '\t' {
							hasNonWhitespace = true
							break
						}
					}
				}
				if !hasNull && strings.Contains(chunk, "\x00") {
					hasNull = true
				}
				searchBuf := patternCarry + chunk
				if !isPII {
					hasDigit := containsDigit(searchBuf)
					hasAt := strings.Contains(searchBuf, "@")
					if hasDigit {
						if ssnRe.MatchString(searchBuf) || phoneRe1.MatchString(searchBuf) || phoneRe2.MatchString(searchBuf) || ccRe.MatchString(searchBuf) {
							isPII = true
						}
					}
					if !isPII && hasAt {
						if emailRe.MatchString(searchBuf) {
							isPII = true
						}
					}
				}
				if !isBiz {
					low := strings.ToLower(searchBuf)
					for _, kw := range keywords {
						if strings.Contains(low, kw) {
							isBiz = true
							break
						}
					}
				}
				combined := lineRemainder + chunk
				parts := strings.Split(combined, "\n")
				for i := 0; i < len(parts)-1; i++ {
					line := parts[i]
					if strings.TrimSpace(line) == "" {
						continue
					}
					totalLines++
					if logLineRe.MatchString(line) {
						matchedLines++
					}
				}
				lineRemainder = parts[len(parts)-1]
				if len(lineRemainder) > maxLineFrag {
					lineRemainder = lineRemainder[:maxLineFrag]
				}
				if len(chunk) >= overlap {
					patternCarry = chunk[len(chunk)-overlap:]
				} else {
					tmp := patternCarry + chunk
					if len(tmp) > overlap {
						patternCarry = tmp[len(tmp)-overlap:]
					} else {
						patternCarry = tmp
					}
				}
			}
			if err != nil {
				if err == io.EOF {
					break
				}
				break
			}
		}
		f.Close()
		if strings.TrimSpace(lineRemainder) != "" {
			totalLines++
			if logLineRe.MatchString(lineRemainder) {
				matchedLines++
			}
		}

		if !hasNonWhitespace {
			results = append(results, Result{File: path, Category: "non-essential"})
			continue
		}
		if isPII {
			results = append(results, Result{File: path, Category: "pii"})
			continue
		}
		if hasNull {
			results = append(results, Result{File: path, Category: "non-essential"})
			continue
		}
		if totalLines >= 3 && float64(matchedLines)/float64(totalLines) > 0.5 {
			results = append(results, Result{File: path, Category: "non-essential"})
			continue
		}
		if isBiz {
			results = append(results, Result{File: path, Category: "business-critical"})
		} else {
			results = append(results, Result{File: path, Category: "non-essential"})
		}
	}

	sort.Slice(results, func(i, j int) bool { return results[i].File < results[j].File })
	if results == nil {
		results = []Result{}
	}
	data, _ := json.MarshalIndent(results, "", "  ")
	tmpFile, _ := os.CreateTemp(outDir, "out-*.tmp")
	tmpPath := tmpFile.Name()
	tmpFile.Write(data)
	tmpFile.Close()
	os.Rename(tmpPath, *outputFlag)
}
GOMAIN

go build -o file-analyzer .
echo "Solution built for step1"
