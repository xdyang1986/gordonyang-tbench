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
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
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
  --dir string
  --output string
  --help`)
}

const (
	bufSize     = 32 * 1024
	overlap     = 256
	maxLineFrag = 8192
)

func isWordChar(c byte) bool {
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'
}
func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func findSSN(b []byte) bool {
	for i := 0; i+11 <= len(b); i++ {
		if i > 0 && isWordChar(b[i-1]) {
			continue
		}
		if !isDigit(b[i]) || !isDigit(b[i+1]) || !isDigit(b[i+2]) || b[i+3] != '-' || !isDigit(b[i+4]) || !isDigit(b[i+5]) || b[i+6] != '-' || !isDigit(b[i+7]) || !isDigit(b[i+8]) || !isDigit(b[i+9]) || !isDigit(b[i+10]) {
			continue
		}
		if i+11 < len(b) && isWordChar(b[i+11]) {
			continue
		}
		return true
	}
	return false
}

func findEmail(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if b[i] == '@' {
			start := i - 1
			for start >= 0 {
				c := b[start]
				if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '%' || c == '+' || c == '-' {
					start--
				} else {
					break
				}
			}
			start++
			if i-start < 1 {
				continue
			}
			end := i + 1
			for end < len(b) {
				c := b[end]
				if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-' {
					end++
				} else {
					break
				}
			}
			domain := b[i+1 : end]
			if !bytes.Contains(domain, []byte(".")) {
				continue
			}
			if bytes.Contains(b[start:end], []byte("..")) {
				continue
			}
			lastDot := bytes.LastIndex(domain, []byte("."))
			if lastDot == -1 {
				continue
			}
			tld := domain[lastDot+1:]
			if len(tld) < 2 {
				continue
			}
			if end < len(b) && isWordChar(b[end]) {
				continue
			}
			return true
		}
	}
	return false
}

func findPhone(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if i+13 < len(b) && b[i] == '(' && isDigit(b[i+1]) && isDigit(b[i+2]) && isDigit(b[i+3]) && b[i+4] == ')' {
			j := i + 5
			if j < len(b) && b[j] == ' ' {
				j++
			}
			if j+7 < len(b) && isDigit(b[j]) && isDigit(b[j+1]) && isDigit(b[j+2]) && (b[j+3] == '-' || b[j+3] == '.') && isDigit(b[j+4]) && isDigit(b[j+5]) && isDigit(b[j+6]) && isDigit(b[j+7]) {
				return true
			}
		}
		if i+9 >= len(b) {
			continue
		}
		if !isDigit(b[i]) {
			continue
		}
		if i > 0 && isWordChar(b[i-1]) {
			continue
		}
		pos := i
		if pos+2 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) {
			continue
		}
		pos += 3
		if pos < len(b) && (b[pos] == '-' || b[pos] == '.') {
			pos++
		}
		if pos+2 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) {
			continue
		}
		pos += 3
		if pos < len(b) && (b[pos] == '-' || b[pos] == '.') {
			pos++
		}
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		if pos < len(b) && isWordChar(b[pos]) {
			continue
		}
		return true
	}
	return false
}

func findCC(b []byte) bool {
	for i := 0; i+19 <= len(b); i++ {
		if i > 0 && isWordChar(b[i-1]) {
			continue
		}
		pos := i
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		if pos < len(b) && (b[pos] == '-' || b[pos] == ' ') {
			pos++
		}
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		if pos < len(b) && (b[pos] == '-' || b[pos] == ' ') {
			pos++
		}
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		if pos < len(b) && (b[pos] == '-' || b[pos] == ' ') {
			pos++
		}
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		if pos < len(b) && isWordChar(b[pos]) {
			continue
		}
		return true
	}
	return false
}

func isLogLine(line []byte) bool {
	if len(line) >= 10 {
		if isDigit(line[0]) && isDigit(line[1]) && isDigit(line[2]) && isDigit(line[3]) && line[4] == '-' && isDigit(line[5]) && isDigit(line[6]) && line[7] == '-' && isDigit(line[8]) && isDigit(line[9]) {
			return true
		}
	}
	if len(line) >= 8 {
		if isDigit(line[0]) && isDigit(line[1]) && line[2] == ':' && isDigit(line[3]) && isDigit(line[4]) && line[5] == ':' && isDigit(line[6]) && isDigit(line[7]) {
			return true
		}
	}
	low := bytes.ToLower(line)
	if bytes.Contains(low, []byte("info")) || bytes.Contains(low, []byte("debug")) || bytes.Contains(low, []byte("warn")) || bytes.Contains(low, []byte("error")) || bytes.Contains(low, []byte("trace")) {
		return true
	}
	return false
}

func containsFold(haystack, needle []byte) bool {
	n := len(needle)
	if n == 0 {
		return true
	}
	for i := 0; i+n <= len(haystack); i++ {
		match := true
		for j := 0; j < n; j++ {
			h := haystack[i+j]
			if h >= 'A' && h <= 'Z' {
				h += 'a' - 'A'
			}
			if h != needle[j] {
				match = false
				break
			}
		}
		if match {
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
		if a == "--help" || a == "-h" || a == "help" {
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
	dirFlag := fset.String("dir", "", "")
	outputFlag := fset.String("output", "", "")
	fset.SetOutput(os.Stderr)
	fset.Parse(args)
	if *dirFlag == "" || *outputFlag == "" {
		os.Exit(2)
	}
	info, err := os.Stat(*dirFlag)
	if err != nil || !info.IsDir() {
		os.Exit(2)
	}
	outDir := filepath.Dir(*outputFlag)
	os.MkdirAll(outDir, 0755)

	keywords := [][]byte{
		[]byte("confidential"), []byte("proprietary"), []byte("trade secret"), []byte("financial"), []byte("revenue"), []byte("budget"),
		[]byte("forecast"), []byte("strategic"), []byte("merger"), []byte("acquisition"), []byte("contract"), []byte("nda"),
		[]byte("intellectual property"), []byte("earnings"), []byte("profit"), []byte("balance sheet"), []byte("board meeting"), []byte("shareholder"),
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
		lineRemainder := []byte{}
		patternCarry := []byte{}
		buf := make([]byte, bufSize)

		for {
			n, err := f.Read(buf)
			if n > 0 {
				chunk := buf[:n]
				if !hasNonWhitespace {
					for _, b := range chunk {
						if b != ' ' && b != '\n' && b != '\r' && b != '\t' {
							hasNonWhitespace = true
							break
						}
					}
				}
				if !hasNull && bytes.Contains(chunk, []byte{0}) {
					hasNull = true
				}
				if !isPII {
					if findSSN(chunk) || findEmail(chunk) || findPhone(chunk) || findCC(chunk) {
						isPII = true
					} else if len(patternCarry) > 0 {
						boundaryEnd := overlap
						if boundaryEnd > len(chunk) {
							boundaryEnd = len(chunk)
						}
						boundary := make([]byte, 0, len(patternCarry)+boundaryEnd)
						boundary = append(boundary, patternCarry...)
						boundary = append(boundary, chunk[:boundaryEnd]...)
						if findSSN(boundary) || findEmail(boundary) || findPhone(boundary) || findCC(boundary) {
							isPII = true
						}
					}
				}
				if !isBiz {
					for _, kw := range keywords {
						if containsFold(chunk, kw) {
							isBiz = true
							break
						}
					}
					if !isBiz && len(patternCarry) > 0 {
						boundaryEnd := overlap
						if boundaryEnd > len(chunk) {
							boundaryEnd = len(chunk)
						}
						boundary := make([]byte, 0, len(patternCarry)+boundaryEnd)
						boundary = append(boundary, patternCarry...)
						boundary = append(boundary, chunk[:boundaryEnd]...)
						for _, kw := range keywords {
							if containsFold(boundary, kw) {
								isBiz = true
								break
							}
						}
					}
				}

				combined := append(lineRemainder, chunk...)
				start := 0
				for {
					idx := bytes.IndexByte(combined[start:], '\n')
					if idx == -1 {
						break
					}
					line := combined[start : start+idx]
					start = start + idx + 1
					if len(bytes.TrimSpace(line)) == 0 {
						continue
					}
					totalLines++
					if isLogLine(line) {
						matchedLines++
					}
				}
				lineRemainder = combined[start:]
				if len(lineRemainder) > maxLineFrag {
					lineRemainder = lineRemainder[:maxLineFrag]
				}

				if len(chunk) >= overlap {
					patternCarry = append(patternCarry[:0], chunk[len(chunk)-overlap:]...)
				} else {
					tmp := append(patternCarry, chunk...)
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
		if len(bytes.TrimSpace(lineRemainder)) != 0 {
			totalLines++
			if isLogLine(lineRemainder) {
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
