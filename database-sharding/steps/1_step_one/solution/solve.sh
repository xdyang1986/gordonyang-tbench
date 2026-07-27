#!/bin/bash
set -e

# Reference solution for Turn 1: Sharding Proxy in Go without legacy handling

cat > /app/go.mod << 'GO'
module sharding

go 1.22
GO

cat > /app/main.go << 'GO'
package main

import (
	"crypto/md5"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
)

type Shard struct {
	ID   int    `json:"id"`
	Path string `json:"path"`
}

type Config struct {
	ShardCount int     `json:"shard_count"`
	Shards     []Shard `json:"shards"`
}

type ShardingProxy struct {
	ConfigPath string
	Config     Config
}

func NewShardingProxy(configPath string) (*ShardingProxy, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	for _, s := range cfg.Shards {
		dir := filepath.Dir(s.Path)
		if err := os.MkdirAll(dir, 0755); err != nil {
			return nil, err
		}
		if _, err := os.Stat(s.Path); os.IsNotExist(err) {
			if err := atomicWrite(s.Path, map[string]interface{}{}); err != nil {
				return nil, err
			}
		} else {
			// ensure valid JSON dict, if not, reset to {}
			b, err := os.ReadFile(s.Path)
			if err != nil {
				continue
			}
			trim := strings.TrimSpace(string(b))
			if trim == "" {
				atomicWrite(s.Path, map[string]interface{}{})
				continue
			}
			var m map[string]interface{}
			if err := json.Unmarshal([]byte(trim), &m); err != nil {
				atomicWrite(s.Path, map[string]interface{}{})
			}
		}
	}
	return &ShardingProxy{ConfigPath: configPath, Config: cfg}, nil
}

func hashKey(key string, shardCount int) int {
	h := md5.Sum([]byte(key))
	bi := new(big.Int).SetBytes(h[:])
	mod := new(big.Int).Mod(bi, big.NewInt(int64(shardCount)))
	return int(mod.Int64())
}

func (p *ShardingProxy) GetShardID(key string) int {
	return hashKey(key, p.Config.ShardCount)
}

func (p *ShardingProxy) GetShardPath(key string) (string, error) {
	sid := p.GetShardID(key)
	for _, s := range p.Config.Shards {
		if s.ID == sid {
			return s.Path, nil
		}
	}
	return "", fmt.Errorf("shard id %d not found", sid)
}

func (p *ShardingProxy) readShard(path string) (map[string]interface{}, error) {
	// if not exists, return empty
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return map[string]interface{}{}, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]interface{}{}, nil
		}
		return nil, err
	}
	trim := strings.TrimSpace(string(b))
	if trim == "" {
		return map[string]interface{}{}, nil
	}
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(trim), &m); err != nil {
		// treat invalid as empty
		return map[string]interface{}{}, nil
	}
	return m, nil
}

func atomicWrite(path string, data map[string]interface{}) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmpFile, err := os.CreateTemp(dir, "tmp-*.json")
	if err != nil {
		return err
	}
	tmpPath := tmpFile.Name()
	enc := json.NewEncoder(tmpFile)
	enc.SetIndent("", "  ")
	if err := enc.Encode(data); err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		return err
	}
	tmpFile.Close()
	return os.Rename(tmpPath, path)
}

func (p *ShardingProxy) Get(key string) (interface{}, bool) {
	path, err := p.GetShardPath(key)
	if err != nil {
		return nil, false
	}
	m, err := p.readShard(path)
	if err != nil {
		return nil, false
	}
	v, ok := m[key]
	return v, ok
}

func (p *ShardingProxy) Set(key string, value interface{}) error {
	path, err := p.GetShardPath(key)
	if err != nil {
		return err
	}
	m, err := p.readShard(path)
	if err != nil {
		return err
	}
	m[key] = value
	return atomicWrite(path, m)
}

func (p *ShardingProxy) Delete(key string) (bool, error) {
	path, err := p.GetShardPath(key)
	if err != nil {
		return false, err
	}
	m, err := p.readShard(path)
	if err != nil {
		return false, err
	}
	if _, ok := m[key]; ok {
		delete(m, key)
		if err := atomicWrite(path, m); err != nil {
			return false, err
		}
		return true, nil
	}
	return false, nil
}

func (p *ShardingProxy) GetAllKeys() ([]string, error) {
	keys := []string{}
	for _, s := range p.Config.Shards {
		m, err := p.readShard(s.Path)
		if err != nil {
			return nil, err
		}
		for k := range m {
			keys = append(keys, k)
		}
	}
	return keys, nil
}

func (p *ShardingProxy) GetShardDistribution() (map[int]int, error) {
	dist := make(map[int]int)
	for _, s := range p.Config.Shards {
		m, err := p.readShard(s.Path)
		if err != nil {
			return nil, err
		}
		dist[s.ID] = len(m)
	}
	return dist, nil
}

func printHelp() {
	fmt.Println(`Usage:
  proxy --config /app/config.json [--legacy /app/data/legacy.json] <command> [args]

Commands:
  get-shard-id <key>          prints shard id
  get-shard-path <key>        prints shard path
  get <key>                   prints JSON value or null
  set <key> <value_json>      stores JSON value
  delete <key>                prints true/false
  list-keys                   prints JSON array of keys
  distribution                prints JSON map shard_id->count
  migrate [--dry-run] [--backup path] [--force]   migrates legacy (turn2)

Flags:
  --config string (default /app/config.json)
  --legacy string (default /app/data/legacy.json) -- used in turn2 fallback and migrate
  --dry-run   for migrate
  --backup    path for migrate
  --force     for migrate
`)
}

func main() {
	// Global flag parsing (allow --config and --legacy anywhere)
	configPath := "/app/config.json"
	legacyPath := "/app/data/legacy.json"
	dryRun := false
	backupPath := ""
	force := false

	// First pass: extract flags
	args := os.Args[1:]
	filtered := []string{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--config" && i+1 < len(args) {
			configPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--config=") {
			configPath = strings.TrimPrefix(a, "--config=")
		} else if a == "--legacy" && i+1 < len(args) {
			legacyPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--legacy=") {
			legacyPath = strings.TrimPrefix(a, "--legacy=")
		} else if a == "--dry-run" {
			dryRun = true
			// keep for migrate parsing? also need to keep flag for later? We'll keep in filtered if it's for migrate
			filtered = append(filtered, a)
		} else if a == "--backup" && i+1 < len(args) {
			backupPath = args[i+1]
			// keep for migrate
			filtered = append(filtered, a, args[i+1])
			i++
		} else if strings.HasPrefix(a, "--backup=") {
			backupPath = strings.TrimPrefix(a, "--backup=")
			filtered = append(filtered, a)
		} else if a == "--force" {
			force = true
			filtered = append(filtered, a)
		} else if a == "--help" || a == "-h" {
			printHelp()
			return
		} else {
			filtered = append(filtered, a)
		}
	}

	// For migration we need to handle separately, but also proxy needs config
	_ = legacyPath
	_ = dryRun
	_ = backupPath
	_ = force

	if len(filtered) == 0 {
		printHelp()
		os.Exit(2)
	}

	// Initialize proxy (for all commands except maybe help)
	proxy, err := NewShardingProxy(configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to init proxy: %v\n", err)
		os.Exit(1)
	}

	cmd := filtered[0]
	cmdArgs := filtered[1:]

	switch cmd {
	case "get-shard-id":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "get-shard-id requires <key>")
			os.Exit(2)
		}
		sid := proxy.GetShardID(cmdArgs[0])
		fmt.Println(sid)
	case "get-shard-path":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "get-shard-path requires <key>")
			os.Exit(2)
		}
		path, err := proxy.GetShardPath(cmdArgs[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(path)
	case "get":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "get requires <key>")
			os.Exit(2)
		}
		val, ok := proxy.Get(cmdArgs[0])
		if !ok {
			fmt.Println("null")
			return
		}
		b, err := json.Marshal(val)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Marshal error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(string(b))
	case "set":
		if len(cmdArgs) < 2 {
			fmt.Fprintln(os.Stderr, "set requires <key> <value_json>")
			os.Exit(2)
		}
		key := cmdArgs[0]
		valueStr := cmdArgs[1]
		var v interface{}
		if err := json.Unmarshal([]byte(valueStr), &v); err != nil {
			// treat as raw string if not JSON
			v = valueStr
		}
		if err := proxy.Set(key, v); err != nil {
			fmt.Fprintf(os.Stderr, "Set error: %v\n", err)
			os.Exit(1)
		}
	case "delete":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "delete requires <key>")
			os.Exit(2)
		}
		deleted, err := proxy.Delete(cmdArgs[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "Delete error: %v\n", err)
			os.Exit(1)
		}
		if deleted {
			fmt.Println("true")
		} else {
			fmt.Println("false")
		}
	case "list-keys", "get-all-keys":
		keys, err := proxy.GetAllKeys()
		if err != nil {
			fmt.Fprintf(os.Stderr, "list-keys error: %v\n", err)
			os.Exit(1)
		}
		b, _ := json.Marshal(keys)
		fmt.Println(string(b))
	case "distribution", "get-shard-distribution":
		dist, err := proxy.GetShardDistribution()
		if err != nil {
			fmt.Fprintf(os.Stderr, "distribution error: %v\n", err)
			os.Exit(1)
		}
		// marshal as map[string]int for JSON compatibility
		m := make(map[string]int)
		for k, v := range dist {
			m[fmt.Sprintf("%d", k)] = v
		}
		b, _ := json.Marshal(m)
		fmt.Println(string(b))
	case "migrate":
		fmt.Fprintln(os.Stderr, "migrate not implemented in turn1")
		os.Exit(1)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
GO

echo "Turn1 Go solution applied"

# Reset shards to empty (tests will reset anyway)
python3 -c "
import json
cfg=json.load(open('/app/config.json'))
for s in cfg['shards']:
    open(s['path'],'w').write('{}')
print('Shards reset')
"

# Verify build
go build -o /tmp/proxy . && echo "Build OK"
