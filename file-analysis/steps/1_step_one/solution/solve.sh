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
		"--dir": true, "--output": true, "--help": true, "-h": true,
	}
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

	results := []Result{}

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
		contentBytes, err := os.ReadFile(path)
		if err != nil {
			results = append(results, Result{File: path, Category: "non-essential"})
			return nil
		}
		content := string(contentBytes)
		trimmed := strings.TrimSpace(content)
		if trimmed == "" {
			results = append(results, Result{File: path, Category: "non-essential"})
			return nil
		}

		isPII := false
		if ssnRe.MatchString(content) || emailRe.MatchString(content) || phoneRe1.MatchString(content) || phoneRe2.MatchString(content) || ccRe.MatchString(content) {
			isPII = true
		}
		if isPII {
			results = append(results, Result{File: path, Category: "pii"})
			return nil
		}

		if strings.Contains(content, "\x00") {
			results = append(results, Result{File: path, Category: "non-essential"})
			return nil
		}

		lines := strings.Split(content, "\n")
		if len(lines) >= 3 {
			nonEmpty := 0
			matched := 0
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
				results = append(results, Result{File: path, Category: "non-essential"})
				return nil
			}
		}

		lower := strings.ToLower(content)
		isBiz := false
		for _, kw := range keywords {
			if strings.Contains(lower, kw) {
				isBiz = true
				break
			}
		}
		if isBiz {
			results = append(results, Result{File: path, Category: "business-critical"})
		} else {
			results = append(results, Result{File: path, Category: "non-essential"})
		}
		return nil
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "walk error: %v\n", err)
		os.Exit(2)
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].File < results[j].File
	})

	if results == nil {
		results = []Result{}
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
echo "Solution built for step1"
