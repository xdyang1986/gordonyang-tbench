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
	"runtime"
	"sort"
	"strconv"
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
	bufSize = 1 * 1024 * 1024
	overlap = 256
	maxLineFrag = 8192
)

func isValidSSN(ssn string) bool {
	parts := strings.Split(ssn, "-")
	if len(parts) != 3 {
		return false
	}
	area, err1 := strconv.Atoi(parts[0])
	group, err2 := strconv.Atoi(parts[1])
	serial, err3 := strconv.Atoi(parts[2])
	if err1 != nil || err2 != nil || err3 != nil {
		return false
	}
	if area == 0 || area == 666 || area >= 900 || group == 0 || serial == 0 {
		return false
	}
	return true
}

func luhnCheck(s string) bool {
	sum := 0
	alt := false
	for i := len(s) - 1; i >= 0; i-- {
		d := int(s[i] - '0')
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

func isAllSameDigit(s string) bool {
	if len(s) == 0 {
		return false
	}
	for i := 1; i < len(s); i++ {
		if s[i] != s[0] {
			return false
		}
	}
	return true
}

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

	ssnRe := regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`)
	emailRe := regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`)
	phoneRe1 := regexp.MustCompile(`\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`)
	phoneRe2 := regexp.MustCompile(`\(\d{3}\)\s*\d{3}[-.]\d{4}`)
	ccStrictRe := regexp.MustCompile(`\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`)
	ccRe := regexp.MustCompile(`\b(?:\d[-\s]*){13,19}\b`)
	finDollarRe := regexp.MustCompile(`\$\s*\d`)
	finPercentRe := regexp.MustCompile(`\b\d+(\.\d+)?\s*%`)
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

	// Global log heavy check
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
			distinct := 0
			totalOcc := 0
			seenKw := map[string]bool{}
			bizReasons := []string{}
			hasFin := false
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
							for _, m := range ssnRe.FindAllString(searchBuf, -1) {
								if isValidSSN(m) {
									isPII = true
									reasonsPII = append(reasonsPII, fmt.Sprintf("ssn pattern %s", m))
								}
							}
							if !isPII {
								for _, mm := range phoneRe1.FindAllString(searchBuf, -1) {
									digits := regexp.MustCompile(`\D`).ReplaceAllString(mm, "")
									if len(digits) >= 10 && !isAllSameDigit(digits) {
										isPII = true
										reasonsPII = append(reasonsPII, fmt.Sprintf("phone pattern %s", mm))
									}
								}
							}
							if !isPII {
								for _, mm := range phoneRe2.FindAllString(searchBuf, -1) {
									digits := regexp.MustCompile(`\D`).ReplaceAllString(mm, "")
									if len(digits) >= 10 && !isAllSameDigit(digits) {
										isPII = true
										reasonsPII = append(reasonsPII, fmt.Sprintf("phone pattern %s", mm))
									}
								}
							}
							if !isPII {
								cands := ccStrictRe.FindAllString(searchBuf, -1)
								if len(cands) == 0 {
									cands = ccRe.FindAllString(searchBuf, -1)
								}
								for _, cand := range cands {
									if phoneRe1.MatchString(cand) || phoneRe2.MatchString(cand) {
										continue
									}
									digits := regexp.MustCompile(`\D`).ReplaceAllString(cand, "")
									if len(digits) < 13 || len(digits) > 19 || isAllSameDigit(digits) || !luhnCheck(digits) {
										continue
									}
									isPII = true
									reasonsPII = append(reasonsPII, fmt.Sprintf("valid credit card via Luhn %s", cand))
								}
							}
						}
						if !isPII && hasAt {
							for _, m := range emailRe.FindAllString(searchBuf, -1) {
								if strings.Contains(m, "..") {
									continue
								}
								parts := strings.Split(m, "@")
								if len(parts) != 2 || !strings.Contains(parts[1], ".") {
									continue
								}
								isPII = true
								reasonsPII = append(reasonsPII, fmt.Sprintf("email pattern %s", m))
							}
						}
					}

					// Business keyword tracking with overlap
					lowSearch := strings.ToLower(searchBuf)
					for _, kw := range keywords {
						c := strings.Count(lowSearch, kw)
						if c > 0 {
							if !seenKw[kw] {
								distinct++
								seenKw[kw] = true
							}
							totalOcc += c
						}
					}
					if !hasFin {
						if finDollarRe.MatchString(searchBuf) || finPercentRe.MatchString(searchBuf) {
							hasFin = true
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

			// Build biz reasons for confidence output
			for kw := range seenKw {
				// approximate count already tracked via distinct, but we need per keyword reasons
				// we already counted distinct, for reasons we list seen keywords
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
