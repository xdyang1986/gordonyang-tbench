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
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
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
  --help, -h        Show help
  help              Show help

Examples:
  file-analyzer --dir /tmp/data --output /tmp/out.json
  file-analyzer --dir /tmp/data --output /tmp/out.json --workers 4
  file-analyzer --help`)
}

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
	if area == 0 || area == 666 || area >= 900 {
		return false
	}
	if group == 0 {
		return false
	}
	if serial == 0 {
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

	known := map[string]bool{
		"--dir": true, "--output": true, "--help": true, "-h": true, "--workers": true,
	}
	for _, a := range args {
		if strings.HasPrefix(a, "-") {
			key := a
			if idx := strings.Index(a, "="); idx != -1 {
				key = a[:idx]
			}
			if !known[key] {
				if strings.HasPrefix(a, "--dir=") || strings.HasPrefix(a, "--output=") || strings.HasPrefix(a, "--workers=") {
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
	ccRe := regexp.MustCompile(`\b(?:\d[-\s]*){13,19}\b`)
	ccStrictRe := regexp.MustCompile(`\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`)
	financialDollarRe := regexp.MustCompile(`\$\s*\d`)
	financialPercentRe := regexp.MustCompile(`\b\d+(\.\d+)?\s*%`)
	logLineRe := regexp.MustCompile(`(?i)(^\d{4}-\d{2}-\d{2}|^\[?\d{2}:\d{2}:\d{2}|\b(INFO|DEBUG|WARN|ERROR|TRACE)\b)`)

	keywords := []string{
		"confidential",
		"proprietary",
		"trade secret",
		"financial",
		"revenue",
		"budget",
		"forecast",
		"strategic",
		"merger",
		"acquisition",
		"contract",
		"nda",
		"intellectual property",
		"earnings",
		"profit",
		"balance sheet",
		"board meeting",
		"shareholder",
	}

	extNonEssential := map[string]bool{
		".log": true, ".tmp": true, ".cache": true, ".bak": true, ".old": true, ".swp": true, ".temp": true,
	}

	var filePaths []string
	err = filepath.WalkDir(*dirFlag, func(path string, d fs.DirEntry, err error) error {
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
	if err != nil {
		fmt.Fprintf(os.Stderr, "walk error: %v\n", err)
		os.Exit(2)
	}

	jobs := make(chan string, len(filePaths))
	resultsCh := make(chan Result, len(filePaths))
	var wg sync.WaitGroup

	worker := func() {
		defer wg.Done()
		for path := range jobs {
			f, err := os.Open(path)
			if err != nil {
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: 0.6, Reasons: []string{"unreadable file"}}
				continue
			}
			reader := bufio.NewReader(f)
			var sb strings.Builder
			buf := make([]byte, 4096)
			hasNull := false
			for {
				n, err := reader.Read(buf)
				if n > 0 {
					chunk := buf[:n]
					for _, b := range chunk {
						if b == 0 {
							hasNull = true
							break
						}
					}
					sb.Write(chunk)
				}
				if err != nil {
					break
				}
			}
			f.Close()
			content := sb.String()
			trimmed := strings.TrimSpace(content)
			ext := strings.ToLower(filepath.Ext(path))
			isExtNonEssential := extNonEssential[ext]

			if trimmed == "" {
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: 0.95, Reasons: []string{"empty file"}}
				continue
			}

			reasonsPII := []string{}
			isPII := false

			ssnMatches := ssnRe.FindAllString(content, -1)
			for _, m := range ssnMatches {
				if isValidSSN(m) {
					isPII = true
					reasonsPII = append(reasonsPII, fmt.Sprintf("ssn pattern %s", m))
				}
			}

			emailMatches := emailRe.FindAllString(content, -1)
			for _, m := range emailMatches {
				if strings.Contains(m, "..") {
					continue
				}
				parts := strings.Split(m, "@")
				if len(parts) != 2 {
					continue
				}
				domain := parts[1]
				if !strings.Contains(domain, ".") {
					continue
				}
				if len(domain) < 3 {
					continue
				}
				isPII = true
				reasonsPII = append(reasonsPII, fmt.Sprintf("email pattern %s", m))
			}

			if phoneRe1.MatchString(content) || phoneRe2.MatchString(content) {
				matches1 := phoneRe1.FindAllString(content, -1)
				for _, mm := range matches1 {
					digits := regexp.MustCompile(`\D`).ReplaceAllString(mm, "")
					if len(digits) >= 10 && !isAllSameDigit(digits) {
						isPII = true
						reasonsPII = append(reasonsPII, fmt.Sprintf("phone pattern %s", mm))
					}
				}
				matches2 := phoneRe2.FindAllString(content, -1)
				for _, mm := range matches2 {
					digits := regexp.MustCompile(`\D`).ReplaceAllString(mm, "")
					if len(digits) >= 10 && !isAllSameDigit(digits) {
						isPII = true
						reasonsPII = append(reasonsPII, fmt.Sprintf("phone pattern %s", mm))
					}
				}
			}

			ccCandidates := ccStrictRe.FindAllString(content, -1)
			if len(ccCandidates) == 0 {
				ccCandidates = ccRe.FindAllString(content, -1)
			}
			for _, cand := range ccCandidates {
				digits := regexp.MustCompile(`\D`).ReplaceAllString(cand, "")
				if len(digits) < 13 || len(digits) > 19 {
					continue
				}
				if isAllSameDigit(digits) {
					continue
				}
				if !luhnCheck(digits) {
					continue
				}
				isPII = true
				reasonsPII = append(reasonsPII, fmt.Sprintf("valid credit card via Luhn %s", cand))
			}

			if isPII {
				conf := 0.9
				if len(reasonsPII) >= 2 {
					conf = 0.95
				}
				resultsCh <- Result{File: path, Category: "pii", Confidence: conf, Reasons: reasonsPII}
				continue
			}

			if hasNull {
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: 0.85, Reasons: []string{"binary file detected"}}
				continue
			}

			lower := strings.ToLower(content)
			distinct := 0
			totalOcc := 0
			bizReasons := []string{}
			seenKw := map[string]bool{}
			for _, kw := range keywords {
				count := strings.Count(lower, kw)
				if count > 0 {
					if !seenKw[kw] {
						distinct++
						seenKw[kw] = true
					}
					totalOcc += count
					bizReasons = append(bizReasons, fmt.Sprintf("keyword '%s' found %d times", kw, count))
				}
			}

			hasFinancial := financialDollarRe.MatchString(content) || financialPercentRe.MatchString(content)
			if hasFinancial {
				bizReasons = append(bizReasons, "financial pattern detected")
			}

			isBiz := false
			if distinct >= 2 || totalOcc >= 2 || (distinct >= 1 && hasFinancial) {
				isBiz = true
			}

			isLog := false
			lines := strings.Split(content, "\n")
			if len(lines) >= 3 {
				matched := 0
				nonEmpty := 0
				for _, line := range lines {
					if strings.TrimSpace(line) == "" {
						continue
					}
					nonEmpty++
					if logLineRe.MatchString(line) {
						matched++
					}
				}
				if nonEmpty > 0 && float64(matched)/float64(nonEmpty) > 0.5 {
					isLog = true
				}
			}

			if isExtNonEssential {
				conf := 0.9
				reason := []string{fmt.Sprintf("extension %s", ext)}
				if isLog {
					reason = append(reason, "log pattern detected")
					conf = 0.8
				}
				if isBiz {
					reason = append(reason, bizReasons...)
					reason = append(reason, "but overridden by extension rule")
				}
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: conf, Reasons: reason}
				continue
			}

			if isLog {
				if isBiz && distinct >= 2 && hasFinancial {
					conf := 0.6 + float64(distinct)*0.1
					if conf > 0.9 {
						conf = 0.9
					}
					if hasFinancial {
						conf += 0.05
						if conf > 0.95 {
							conf = 0.95
						}
					}
					resultsCh <- Result{File: path, Category: "business-critical", Confidence: conf, Reasons: append(bizReasons, "log pattern but strong business signals")}
					continue
				}
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: 0.8, Reasons: []string{"log pattern detected", fmt.Sprintf("%d lines log-like", len(lines))}}
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
				if hasFinancial {
					conf += 0.05
					if conf > 0.95 {
						conf = 0.95
					}
				}
				resultsCh <- Result{File: path, Category: "business-critical", Confidence: conf, Reasons: bizReasons}
			} else {
				resultsCh <- Result{File: path, Category: "non-essential", Confidence: 0.6, Reasons: []string{"no sensitive content"}}
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
	sort.Slice(results, func(i, j int) bool {
		return results[i].File < results[j].File
	})
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
	_, err = tmpFile.Write(data)
	if err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		fmt.Fprintf(os.Stderr, "write error: %v\n", err)
		os.Exit(2)
	}
	tmpFile.Close()
	if err := os.Rename(tmpPath, *outputFlag); err != nil {
		os.Remove(tmpPath)
		fmt.Fprintf(os.Stderr, "rename error: %v\n", err)
		os.Exit(2)
	}
}
GOMAIN

go build -o file-analyzer .
echo "Solution built for step2"

