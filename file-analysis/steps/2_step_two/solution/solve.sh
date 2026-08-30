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
	"runtime"
	"sort"
	"strings"
	"sync"
)

type Result struct {
	File       string   `json:"file"`
	Category   string   `json:"category"`
	Confidence float64  `json:"confidence"`
	Reasons    []string `json:"reasons"`
}

func printHelp() {
	fmt.Println(`Usage: file-analyzer --dir <path> --output <path> [options]
File relevance analyzer - classifies files as business-critical, pii, or non-essential with improved accuracy and concurrency

Flags:
  --dir string      Directory to scan recursively
  --output string   Output JSON file path
  --workers int     Number of worker goroutines (default NumCPU)
  --relative        When set, output paths are relative to --dir
  --help, -h        Show help
  help              Show help

Examples:
  file-analyzer --dir ./data --output ./out.json
  file-analyzer --dir ./data --output ./out.json --workers 4 --relative
  file-analyzer --help`)
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
func toLower(c byte) byte {
	if c >= 'A' && c <= 'Z' {
		return c + 'a' - 'A'
	}
	return c
}
func isAllSameDigits(d []byte) bool {
	if len(d) == 0 {
		return false
	}
	for i := 1; i < len(d); i++ {
		if d[i] != d[0] {
			return false
		}
	}
	return true
}
func luhnCheck(digits []byte) bool {
	sum := 0
	alt := false
	for i := len(digits) - 1; i >= 0; i-- {
		d := int(digits[i] - '0')
		if alt {
			d *= 2
			if d > 9 {
				d -= 9
			}
		}
		sum += d
		alt = !alt
	}
	return sum%10 == 0
}

func findValidSSN(b []byte) (bool, string) {
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
		area := int(b[i]-'0')*100 + int(b[i+1]-'0')*10 + int(b[i+2]-'0')
		group := int(b[i+4]-'0')*10 + int(b[i+5]-'0')
		serial := int(b[i+7]-'0')*1000 + int(b[i+8]-'0')*100 + int(b[i+9]-'0')*10 + int(b[i+10]-'0')
		if area == 0 || area == 666 || area >= 900 || group == 0 || serial == 0 {
			continue
		}
		return true, string(b[i : i+11])
	}
	return false, ""
}

func findValidEmail(b []byte) (bool, string) {
	for i := 0; i < len(b); i++ {
		if b[i] != '@' {
			continue
		}
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
		if start > 0 && isWordChar(b[start-1]) {
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
		if end-(i+1) < 1 {
			continue
		}
		email := b[start:end]
		if bytes.Contains(email, []byte("..")) {
			continue
		}
		domain := b[i+1 : end]
		if !bytes.Contains(domain, []byte(".")) {
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
		return true, string(email)
	}
	return false, ""
}

func findValidPhone(b []byte) (bool, string) {
	for i := 0; i < len(b); i++ {
		if !isDigit(b[i]) {
			continue
		}
		if i > 0 && isWordChar(b[i-1]) {
			continue
		}
		if i+2 >= len(b) || !isDigit(b[i+1]) || !isDigit(b[i+2]) {
			continue
		}
		pos := i + 3
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
		var dig []byte
		for k := i; k < pos; k++ {
			if isDigit(b[k]) {
				dig = append(dig, b[k])
			}
		}
		if len(dig) < 10 {
			continue
		}
		if isAllSameDigits(dig) {
			continue
		}
		return true, string(b[i:pos])
	}
	for i := 0; i < len(b); i++ {
		if b[i] != '(' {
			continue
		}
		if i+4 >= len(b) {
			continue
		}
		if !isDigit(b[i+1]) || !isDigit(b[i+2]) || !isDigit(b[i+3]) || b[i+4] != ')' {
			continue
		}
		pos := i + 5
		for pos < len(b) && b[pos] == ' ' {
			pos++
		}
		if pos+2 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) {
			continue
		}
		pos += 3
		if pos >= len(b) || (b[pos] != '-' && b[pos] != '.') {
			continue
		}
		pos++
		if pos+3 >= len(b) || !isDigit(b[pos]) || !isDigit(b[pos+1]) || !isDigit(b[pos+2]) || !isDigit(b[pos+3]) {
			continue
		}
		pos += 4
		var dig []byte
		for k := i; k < pos; k++ {
			if isDigit(b[k]) {
				dig = append(dig, b[k])
			}
		}
		if len(dig) < 10 {
			continue
		}
		if isAllSameDigits(dig) {
			continue
		}
		return true, string(b[i:pos])
	}
	return false, ""
}

func containsPhonePatternInSlice(b []byte) bool {
	ok, _ := findValidPhone(b)
	return ok
}

func findValidCC(b []byte) (bool, string) {
	for i := 0; i < len(b); i++ {
		if !isDigit(b[i]) {
			continue
		}
		if i > 0 && isWordChar(b[i-1]) {
			continue
		}
		digitCount := 0
		var digits []byte
		end := i
		for end < len(b) && end < i+32 {
			c := b[end]
			if isDigit(c) {
				digitCount++
				digits = append(digits, c)
				end++
			} else if c == '-' || c == ' ' {
				end++
			} else {
				break
			}
			if digitCount >= 13 && digitCount <= 19 {
				if end > i && !isDigit(b[end-1]) {
					continue
				}
				if end < len(b) && isWordChar(b[end]) {
					continue
				}
				candSlice := b[i:end]
				if isAllSameDigits(digits) {
					continue
				}
				if !luhnCheck(digits) {
					continue
				}
				if containsPhonePatternInSlice(candSlice) {
					continue
				}
				return true, string(candSlice)
			}
			if digitCount > 19 {
				break
			}
		}
	}
	return false, ""
}

func containsDollar(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if b[i] == '$' {
			j := i + 1
			for j < len(b) && b[j] == ' ' {
				j++
			}
			if j < len(b) && isDigit(b[j]) {
				return true
			}
		}
	}
	return false
}
func containsPercent(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if b[i] == '%' {
			j := i - 1
			for j >= 0 && b[j] == ' ' {
				j--
			}
			if j < 0 {
				continue
			}
			if !isDigit(b[j]) {
				continue
			}
			return true
		}
	}
	return false
}

func isLogLine(b []byte) bool {
	if len(b) >= 10 {
		if isDigit(b[0]) && isDigit(b[1]) && isDigit(b[2]) && isDigit(b[3]) && b[4] == '-' && isDigit(b[5]) && isDigit(b[6]) && b[7] == '-' && isDigit(b[8]) && isDigit(b[9]) {
			return true
		}
	}
	if len(b) >= 8 {
		if isDigit(b[0]) && isDigit(b[1]) && b[2] == ':' && isDigit(b[3]) && isDigit(b[4]) && b[5] == ':' && isDigit(b[6]) && isDigit(b[7]) {
			return true
		}
	}
	low := bytes.ToLower(b)
	if bytes.Contains(low, []byte("info")) || bytes.Contains(low, []byte("debug")) || bytes.Contains(low, []byte("warn")) || bytes.Contains(low, []byte("error")) || bytes.Contains(low, []byte("trace")) {
		return true
	}
	return false
}

func lowerBytes(b []byte) []byte {
	out := make([]byte, len(b))
	for i, c := range b {
		out[i] = toLower(c)
	}
	return out
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
	known := map[string]bool{"--dir": true, "--output": true, "--help": true, "-h": true, "--workers": true, "--relative": true}
	for _, a := range args {
		if strings.HasPrefix(a, "-") {
			key := a
			if idx := strings.Index(a, "="); idx != -1 {
				key = a[:idx]
			}
			if !known[key] {
				if strings.HasPrefix(a, "--dir=") || strings.HasPrefix(a, "--output=") || strings.HasPrefix(a, "--workers=") || strings.HasPrefix(a, "--relative=") {
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
	workersFlag := fset.Int("workers", runtime.NumCPU(), "Number of workers")
	relativeFlag := fset.Bool("relative", false, "Output relative paths")
	fset.SetOutput(os.Stderr)
	if err := fset.Parse(args); err != nil {
		os.Exit(2)
	}
	if *dirFlag == "" || *outputFlag == "" {
		fmt.Fprintln(os.Stderr, "missing required --dir and --output")
		os.Exit(2)
	}
	if *workersFlag <= 0 {
		fmt.Fprintln(os.Stderr, "workers must be >0")
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

	logCount := 0
	for _, p := range filePaths {
		if strings.ToLower(filepath.Ext(p)) == ".log" {
			logCount++
		}
	}
	logHeavy := false
	if len(filePaths) > 0 && float64(logCount)/float64(len(filePaths)) > 0.7 {
		logHeavy = true
	}

	jobs := make(chan string, len(filePaths))
	resultsCh := make(chan Result, len(filePaths))
	var wg sync.WaitGroup

	worker := func() {
		defer wg.Done()
		for path := range jobs {
			ext := strings.ToLower(filepath.Ext(path))
			outFile := path
			if *relativeFlag {
				if rel, err := filepath.Rel(*dirFlag, path); err == nil {
					outFile = rel
				}
			}
			if ext == ".bak" {
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.9, Reasons: []string{"extension .bak precedence inversion: always non-essential even with PII"}}
				continue
			}

			f, err := os.Open(path)
			if err != nil {
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.6, Reasons: []string{"unreadable file"}}
				continue
			}

			hasNonWhitespace := false
			hasNull := false
			isPII := false
			reasonsPII := []string{}
			seenKw := map[string]bool{}
			distinct := 0
			totalOcc := 0
			hasFin := false
			totalLines := 0
			matchedLines := 0
			lineRemainder := []byte{}
			patternCarry := []byte{}
			buf := make([]byte, bufSize)

			for {
				n, rerr := f.Read(buf)
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
					searchBuf := make([]byte, 0, len(patternCarry)+len(chunk))
					searchBuf = append(searchBuf, patternCarry...)
					searchBuf = append(searchBuf, chunk...)

					if !isPII {
						if ok, s := findValidSSN(searchBuf); ok {
							isPII = true
							reasonsPII = append(reasonsPII, fmt.Sprintf("ssn pattern %s", s))
						} else if ok, s := findValidEmail(searchBuf); ok {
							isPII = true
							reasonsPII = append(reasonsPII, fmt.Sprintf("email pattern %s", s))
						} else if ok, s := findValidPhone(searchBuf); ok {
							isPII = true
							reasonsPII = append(reasonsPII, fmt.Sprintf("phone pattern %s", s))
						} else if ok, s := findValidCC(searchBuf); ok {
							isPII = true
							reasonsPII = append(reasonsPII, fmt.Sprintf("valid credit card via Luhn %s", s))
						}
					}

					lowSearch := lowerBytes(searchBuf)
					for _, kw := range keywords {
						kwStr := string(kw)
						kwLen := len(kw)
						idx := 0
						for idx < len(lowSearch) {
							pos := bytes.Index(lowSearch[idx:], kw)
							if pos == -1 {
								break
							}
							absPos := idx + pos
							if absPos+kwLen <= len(patternCarry) {
								idx = absPos + 1
								continue
							}
							if !seenKw[kwStr] {
								seenKw[kwStr] = true
								distinct++
							}
							totalOcc++
							idx = absPos + 1
						}
					}

					if !hasFin {
						if containsDollar(searchBuf) || containsPercent(searchBuf) {
							hasFin = true
						}
					}

					combined := append(lineRemainder, chunk...)
					start := 0
					for {
						nl := bytes.IndexByte(combined[start:], '\n')
						if nl == -1 {
							break
						}
						line := combined[start : start+nl]
						start = start + nl + 1
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
				if rerr != nil {
					if rerr == io.EOF {
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
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.95, Reasons: []string{"empty file"}}
				continue
			}
			if isPII {
				conf := 0.9
				if len(reasonsPII) >= 2 {
					conf = 0.95
				}
				resultsCh <- Result{File: outFile, Category: "pii", Confidence: conf, Reasons: reasonsPII}
				continue
			}
			if hasNull {
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.85, Reasons: []string{"binary file detected"}}
				continue
			}

			bizReasons := []string{}
			for kw := range seenKw {
				bizReasons = append(bizReasons, fmt.Sprintf("keyword '%s' found", kw))
			}
			if hasFin {
				bizReasons = append(bizReasons, "financial pattern detected")
			}
			isBiz := distinct >= 2 || totalOcc >= 2 || (distinct >= 1 && hasFin)

			extNonEss := map[string]bool{".log": true, ".tmp": true, ".cache": true, ".old": true, ".swp": true, ".temp": true}
			if extNonEss[ext] {
				if isBiz {
					resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.9, Reasons: append(bizReasons, fmt.Sprintf("extension %s overrides business", ext))}
					continue
				}
			}

			if totalLines >= 3 && float64(matchedLines)/float64(totalLines) > 0.5 {
				if isBiz && distinct >= 2 && hasFin {
					conf := 0.6 + float64(distinct)*0.1
					if conf > 0.9 {
						conf = 0.9
					}
					conf += 0.05
					if conf > 0.95 {
						conf = 0.95
					}
					resultsCh <- Result{File: outFile, Category: "business-critical", Confidence: conf, Reasons: append(bizReasons, "log pattern but strong business")}
					continue
				}
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.8, Reasons: []string{"predominantly log lines"}}
				continue
			}

			if logHeavy && ext != ".log" && isBiz {
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.85, Reasons: append(bizReasons, "sibling-dependent downgrade: >70% logs in directory")}
				continue
			}

			if isBiz {
				conf := 0.5 + float64(distinct)*0.1
				if conf < 0.6 {
					conf = 0.6
				}
				if conf > 0.9 {
					conf = 0.9
				}
				if hasFin {
					conf += 0.05
					if conf > 0.95 {
						conf = 0.95
					}
				}
				resultsCh <- Result{File: outFile, Category: "business-critical", Confidence: conf, Reasons: bizReasons}
			} else {
				resultsCh <- Result{File: outFile, Category: "non-essential", Confidence: 0.6, Reasons: []string{"no sensitive content"}}
			}
		}
	}

	numWorkers := *workersFlag
	if numWorkers > len(filePaths) && len(filePaths) > 0 {
		numWorkers = len(filePaths)
	}
	wg.Add(numWorkers)
	for i := 0; i < numWorkers; i++ {
		go worker()
	}
	for _, p := range filePaths {
		jobs <- p
	}
	close(jobs)
	wg.Wait()
	close(resultsCh)

	var results []Result
	for r := range resultsCh {
		results = append(results, r)
	}
	sort.Slice(results, func(i, j int) bool { return results[i].File < results[j].File })
	if results == nil {
		results = []Result{}
	}
	for i := range results {
		if results[i].Reasons == nil {
			results[i].Reasons = []string{}
		}
	}
	data, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "marshal error: %v\n", err)
		os.Exit(2)
	}
	tmpFile, err := os.CreateTemp(outDir, "out-*.tmp")
	if err != nil {
		fmt.Fprintf(os.Stderr, "temp file error: %v\n", err)
		os.Exit(2)
	}
	tmpPath := tmpFile.Name()
	tmpFile.Write(data)
	tmpFile.Close()
	os.Rename(tmpPath, *outputFlag)
}
GOMAIN

go build -o file-analyzer .
echo "Solution built for step2"
