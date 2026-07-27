#!/bin/bash
set -e

# Reference solution for Turn 2: Go proxy with legacy fallback + migration

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
	"io"
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
	LegacyPath string
	Config     Config
}

func NewShardingProxy(configPath string) (*ShardingProxy, error) {
	return NewShardingProxyWithLegacy(configPath, "/app/data/legacy.json")
}

func NewShardingProxyWithLegacy(configPath, legacyPath string) (*ShardingProxy, error) {
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
	return &ShardingProxy{ConfigPath: configPath, LegacyPath: legacyPath, Config: cfg}, nil
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
		return map[string]interface{}{}, nil
	}
	return m, nil
}

func (p *ShardingProxy) readLegacy() (map[string]interface{}, error) {
	if _, err := os.Stat(p.LegacyPath); os.IsNotExist(err) {
		return map[string]interface{}{}, nil
	}
	b, err := os.ReadFile(p.LegacyPath)
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
		// if invalid, return empty for fallback robustness, but migration should error
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
	if v, ok := m[key]; ok {
		return v, true
	}
	// fallback to legacy
	leg, err := p.readLegacy()
	if err != nil {
		return nil, false
	}
	v, ok := leg[key]
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
	leg, err := p.readLegacy()
	if err != nil {
		return nil, err
	}
	for k := range leg {
		keysMap[k] = struct{}{}
	}
	keys := make([]string, 0, len(keysMap))
	for k := range keysMap {
		keys = append(keys, k)
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

// Migration logic

func loadConfig(configPath string) (Config, error) {
	var cfg Config
	data, err := os.ReadFile(configPath)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func readJSONMap(path string) (map[string]interface{}, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	trim := strings.TrimSpace(string(b))
	if trim == "" {
		return map[string]interface{}{}, nil
	}
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(trim), &m); err != nil {
		return nil, err
	}
	return m, nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}

func migrate(legacyPath, configPath string, dryRun bool, backupPath string, force bool) error {
	cfg, err := loadConfig(configPath)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Fprintf(os.Stderr, "Config file not found: %s\n", configPath)
		} else {
			fmt.Fprintf(os.Stderr, "Invalid config %s: %v\n", configPath, err)
		}
		return err
	}
	if cfg.ShardCount == 0 || cfg.Shards == nil {
		fmt.Fprintln(os.Stderr, "Invalid config: missing shard_count or shards")
		return fmt.Errorf("invalid config")
	}
	idToPath := make(map[int]string)
	for _, s := range cfg.Shards {
		idToPath[s.ID] = s.Path
	}

	legacyData, err := readJSONMap(legacyPath)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Fprintf(os.Stderr, "Legacy file not found: %s\n", legacyPath)
			return err
		}
		fmt.Fprintf(os.Stderr, "Invalid legacy JSON %s: %v\n", legacyPath, err)
		return err
	}
	if len(legacyData) == 0 {
		fmt.Println("Legacy file is empty, nothing to migrate")
		if backupPath != "" {
			if err := copyFile(legacyPath, backupPath); err != nil {
				fmt.Fprintf(os.Stderr, "Failed to create backup: %v\n", err)
				return err
			}
			fmt.Printf("Backup created at %s\n", backupPath)
		}
		return nil
	}

	grouped := make(map[int]map[string]interface{})
	for k, v := range legacyData {
		sid := hashKey(k, cfg.ShardCount)
		if _, ok := grouped[sid]; !ok {
			grouped[sid] = make(map[string]interface{})
		}
		grouped[sid][k] = v
	}

	total := len(legacyData)
	fmt.Printf("Migration plan: %d keys from %s -> %d shards\n", total, legacyPath, cfg.ShardCount)
	for sid := 0; sid < cfg.ShardCount; sid++ {
		if g, ok := grouped[sid]; ok {
			path := idToPath[sid]
			fmt.Printf("  shard %d (%s): %d keys\n", sid, path, len(g))
		}
	}

	if dryRun {
		fmt.Println("Dry-run enabled, not writing any shard files")
		return nil
	}

	if backupPath != "" {
		if err := os.MkdirAll(filepath.Dir(backupPath), 0755); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to create backup dir: %v\n", err)
			return err
		}
		if err := copyFile(legacyPath, backupPath); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to create backup: %v\n", err)
			return err
		}
		fmt.Printf("Backup of legacy created at %s\n", backupPath)
		for _, s := range cfg.Shards {
			if _, err := os.Stat(s.Path); err == nil {
				bak := s.Path + ".bak"
				_ = copyFile(s.Path, bak)
			}
		}
	}

	for sid, keysDict := range grouped {
		shardPath, ok := idToPath[sid]
		if !ok {
			fmt.Fprintf(os.Stderr, "Shard id %d not found in config, skipping %d keys\n", sid, len(keysDict))
			continue
		}
		current, err := readJSONMap(shardPath)
		if err != nil {
			if os.IsNotExist(err) {
				current = map[string]interface{}{}
			} else {
				// if invalid JSON, treat as empty but continue
				current = map[string]interface{}{}
			}
		}
		merged := make(map[string]interface{})
		for k, v := range current {
			merged[k] = v
		}
		changed := false
		for k, v := range keysDict {
			if _, exists := merged[k]; !exists {
				merged[k] = v
				changed = true
			} else if force {
				merged[k] = v
				changed = true
			}
		}
		if !changed {
			// check if current equals merged (force may have overwritten)
			// For simplicity, compare len and content: if force, we already set changed, else no change
			// Actually for idempotency, if no new keys and not force, skip write
			if !force {
				fmt.Printf("Shard %d already up-to-date (%d keys)\n", sid, len(keysDict))
				continue
			}
			// if force but same values, still consider no change? We'll check deep equality via marshal length?
			// Simple: if merged not different from current, skip
			if len(merged) == len(current) {
				same := true
				for k, v := range keysDict {
					if cv, ok := current[k]; !ok || fmt.Sprintf("%v", cv) != fmt.Sprintf("%v", v) {
						// if force and value different, it's changed
						if force {
							// we already marked changed, but we check again
						} else {
							same = false
						}
					}
				}
				if same && !force {
					fmt.Printf("Shard %d already up-to-date (%d keys)\n", sid, len(keysDict))
					continue
				}
			}
		}
		if err := atomicWrite(shardPath, merged); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to write shard %d: %v\n", sid, err)
			return err
		}
		fmt.Printf("Migrated %d keys to shard %d\n", len(keysDict), sid)
	}
	fmt.Printf("Migration completed: %d keys processed\n", total)
	return nil
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
  migrate [--dry-run] [--backup path] [--force]

Flags:
  --config string (default /app/config.json)
  --legacy string (default /app/data/legacy.json)
  --dry-run   for migrate
  --backup    path for migrate
  --force     for migrate
`)
}

func main() {
	configPath := "/app/config.json"
	legacyPath := "/app/data/legacy.json"
	dryRun := false
	backupPath := ""
	force := false

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
			filtered = append(filtered, a)
		} else if a == "--backup" && i+1 < len(args) {
			backupPath = args[i+1]
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

	if len(filtered) == 0 {
		printHelp()
		os.Exit(2)
	}

	cmd := filtered[0]
	cmdArgs := filtered[1:]

	// For migrate command, we handle separately but still need to parse flags inside cmdArgs for dry-run etc if not already captured globally
	// Re-parse migrate flags that may appear after "migrate"
	if cmd == "migrate" || (len(filtered) > 1 && filtered[0] == "migrate") {
		// Already captured global dryRun/backup/force, but also check cmdArgs
		for i := 0; i < len(cmdArgs); i++ {
			a := cmdArgs[i]
			if a == "--dry-run" {
				dryRun = true
			} else if a == "--backup" && i+1 < len(cmdArgs) {
				backupPath = cmdArgs[i+1]
				i++
			} else if strings.HasPrefix(a, "--backup=") {
				backupPath = strings.TrimPrefix(a, "--backup=")
			} else if a == "--force" {
				force = true
			}
		}
		if err := migrate(legacyPath, configPath, dryRun, backupPath, force); err != nil {
			os.Exit(1)
		}
		return
	}

	proxy, err := NewShardingProxyWithLegacy(configPath, legacyPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to init proxy: %v\n", err)
		os.Exit(1)
	}

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
		m := make(map[string]int)
		for k, v := range dist {
			m[fmt.Sprintf("%d", k)] = v
		}
		b, _ := json.Marshal(m)
		fmt.Println(string(b))
	default:
		// support legacy help for migrate
		if strings.Contains(cmd, "migrate") {
			if err := migrate(legacyPath, configPath, dryRun, backupPath, force); err != nil {
				os.Exit(1)
			}
			return
		}
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
GO

echo "Turn2 Go solution applied"
go build -o /tmp/proxy . && echo "Build OK"
