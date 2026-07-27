#!/bin/bash
set -e

# Reference solution for Turn 1: Go proxy with validation, corruption handling, sorted keys

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
	"sort"
	"strings"
	"time"
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

func validateConfig(cfg Config) error {
	if cfg.ShardCount <= 0 {
		return fmt.Errorf("shard_count must be >0")
	}
	if len(cfg.Shards) == 0 {
		return fmt.Errorf("shards empty")
	}
	seen := make(map[int]bool)
	for _, s := range cfg.Shards {
		if s.ID < 0 {
			return fmt.Errorf("negative shard id %d", s.ID)
		}
		if s.ID >= cfg.ShardCount {
			return fmt.Errorf("shard id %d >= shard_count %d", s.ID, cfg.ShardCount)
		}
		if strings.TrimSpace(s.Path) == "" {
			return fmt.Errorf("empty shard path for id %d", s.ID)
		}
		if seen[s.ID] {
			return fmt.Errorf("duplicate shard id %d", s.ID)
		}
		seen[s.ID] = true
	}
	return nil
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
	if err := validateConfig(cfg); err != nil {
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
				backupPath := fmt.Sprintf("%s.corrupt.%d", s.Path, time.Now().UnixNano())
				_ = copyFile(s.Path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard %d file %s corrupted, backup to %s\n", s.ID, s.Path, backupPath)
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
		backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = copyFile(path, backupPath)
		fmt.Fprintf(os.Stderr, "Warning: shard file %s corrupted, backup to %s: %v\n", path, backupPath, err)
		atomicWrite(path, map[string]interface{}{})
		return map[string]interface{}{}, nil
	}
	return m, nil
}

func copyFile(src, dst string) error {
	b, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}
	return os.WriteFile(dst, b, 0644)
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
	keysMap := make(map[string]struct{})
	for _, s := range p.Config.Shards {
		m, err := p.readShard(s.Path)
		if err != nil {
			return nil, err
		}
		for k := range m {
			keysMap[k] = struct{}{}
		}
	}
	keys := make([]string, 0, len(keysMap))
	for k := range keysMap {
		keys = append(keys, k)
	}
	sort.Strings(keys)
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
  proxy --config /app/config.json <command> [args]

Commands:
  get-shard-id <key>
  get-shard-path <key>
  get <key>
  set <key> <value_json>
  delete <key>
  list-keys
  distribution
`)
}

func main() {
	configPath := "/app/config.json"
	args := os.Args[1:]
	filtered := []string{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--config" && i+1 < len(args) {
			configPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--config=") {
			configPath = strings.TrimPrefix(a, "--config=")
		} else if a == "--help" || a == "-h" {
			printHelp()
			return
		} else {
			filtered = append(filtered, a)
		}
	}
	if len(filtered) == 0 {
		printHelp()
		os.Exit(2)
	}
	proxy, err := NewShardingProxy(configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Invalid config %s: %v\n", configPath, err)
		os.Exit(2)
	}
	cmd := filtered[0]
	cmdArgs := filtered[1:]
	switch cmd {
	case "get-shard-id":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		fmt.Println(proxy.GetShardID(cmdArgs[0]))
	case "get-shard-path":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		path, err := proxy.GetShardPath(cmdArgs[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		fmt.Println(path)
	case "get":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		val, ok := proxy.Get(cmdArgs[0])
		if !ok {
			fmt.Println("null")
			return
		}
		b, _ := json.Marshal(val)
		fmt.Println(string(b))
	case "set":
		if len(cmdArgs) < 2 {
			fmt.Fprintln(os.Stderr, "requires <key> <value_json>")
			os.Exit(2)
		}
		var v interface{}
		if err := json.Unmarshal([]byte(cmdArgs[1]), &v); err != nil {
			v = cmdArgs[1]
		}
		if err := proxy.Set(cmdArgs[0], v); err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
	case "delete":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		del, err := proxy.Delete(cmdArgs[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		if del {
			fmt.Println("true")
		} else {
			fmt.Println("false")
		}
	case "list-keys":
		keys, err := proxy.GetAllKeys()
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		b, _ := json.Marshal(keys)
		fmt.Println(string(b))
	case "distribution":
		dist, err := proxy.GetShardDistribution()
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
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
		fmt.Fprintf(os.Stderr, "Unknown command %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
GO

echo "Turn1 Go solution applied"
go build -o ./proxy_bin . && echo "Build OK" && rm -f ./proxy_bin

python3 -c "
import json
cfg=json.load(open('/app/config.json'))
for s in cfg['shards']:
    open(s['path'],'w').write('{}')
print('Shards reset')
"
