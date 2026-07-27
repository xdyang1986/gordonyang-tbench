#!/bin/bash
set -e

# Reference solution Turn2: Go proxy with legacy fallback, checksum integrity, duplicate cleanup, robust migration

cat > /app/go.mod << 'GO'
module sharding

go 1.22
GO

cat > /app/main.go << 'GO'
package main

import (
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
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

type ShardFile struct {
	Data     map[string]interface{} `json:"data"`
	Checksum string                 `json:"checksum"`
}

type ShardingProxy struct {
	ConfigPath string
	LegacyPath string
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

func computeChecksum(data map[string]interface{}) string {
	if data == nil {
		data = map[string]interface{}{}
	}
	b, _ := json.Marshal(data)
	sum := md5.Sum(b)
	return hex.EncodeToString(sum[:])
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

func copyFileIO(src, dst string) error {
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

func atomicWrite(path string, data map[string]interface{}) error {
	if data == nil {
		data = map[string]interface{}{}
	}
	checksum := computeChecksum(data)
	sf := ShardFile{Data: data, Checksum: checksum}
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
	if err := enc.Encode(sf); err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		return err
	}
	tmpFile.Close()
	return os.Rename(tmpPath, path)
}

func readShardFile(path string) (map[string]interface{}, error) {
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
	var raw map[string]interface{}
	if err := json.Unmarshal(b, &raw); err != nil {
		backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = copyFile(path, backupPath)
		fmt.Fprintf(os.Stderr, "Warning: shard file %s is corrupted (invalid JSON), backup to %s: %v\n", path, backupPath, err)
		_ = atomicWrite(path, map[string]interface{}{})
		return map[string]interface{}{}, nil
	}
	if _, hasData := raw["data"]; hasData {
		var sf ShardFile
		if err := json.Unmarshal(b, &sf); err != nil {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s corrupted (new format), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		if sf.Data == nil {
			sf.Data = map[string]interface{}{}
		}
		expected := computeChecksum(sf.Data)
		if sf.Checksum != "" && expected != sf.Checksum {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s checksum mismatch (corrupt), expected %s got %s, backup to %s\n", path, expected, sf.Checksum, backupPath)
			_ = atomicWrite(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		return sf.Data, nil
	}
	return raw, nil
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
			_, _ = readShardFile(s.Path)
		}
	}
	return &ShardingProxy{ConfigPath: configPath, LegacyPath: legacyPath, Config: cfg}, nil
}

func NewShardingProxy(configPath string) (*ShardingProxy, error) {
	return NewShardingProxyWithLegacy(configPath, "/app/data/legacy.json")
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
		return map[string]interface{}{}, nil
	}
	return m, nil
}

func (p *ShardingProxy) Get(key string) (interface{}, bool) {
	path, err := p.GetShardPath(key)
	if err != nil {
		return nil, false
	}
	m, err := readShardFile(path)
	if err != nil {
		return nil, false
	}
	if v, ok := m[key]; ok {
		return v, true
	}
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
	m, err := readShardFile(path)
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
	m, err := readShardFile(path)
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
		m, err := readShardFile(s.Path)
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
	sort.Strings(keys)
	return keys, nil
}

func (p *ShardingProxy) GetShardDistribution() (map[int]int, error) {
	dist := make(map[int]int)
	for _, s := range p.Config.Shards {
		m, err := readShardFile(s.Path)
		if err != nil {
			return nil, err
		}
		dist[s.ID] = len(m)
	}
	return dist, nil
}

func loadConfig(configPath string) (Config, error) {
	var cfg Config
	data, err := os.ReadFile(configPath)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return cfg, err
	}
	if err := validateConfig(cfg); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func readLegacyMap(path string) (map[string]interface{}, error) {
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
	idToPath := make(map[int]string)
	for _, s := range cfg.Shards {
		idToPath[s.ID] = s.Path
	}

	legacyData, err := readLegacyMap(legacyPath)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Fprintf(os.Stderr, "Legacy file not found: %s\n", legacyPath)
			return err
		}
		fmt.Fprintf(os.Stderr, "Invalid legacy JSON %s: %v\n", legacyPath, err)
		return err
	}

	// Scan all shards for existing data (supports both formats, with corruption handling)
	currentData := make(map[int]map[string]interface{})
	keyToShards := make(map[string][]int)
	for _, s := range cfg.Shards {
		m, err := readShardFile(s.Path)
		if err != nil {
			if os.IsNotExist(err) {
				m = map[string]interface{}{}
			} else {
				m = map[string]interface{}{}
			}
		}
		currentData[s.ID] = m
		for k := range m {
			keyToShards[k] = append(keyToShards[k], s.ID)
		}
	}

	hasDup := false
	for k, shards := range keyToShards {
		if len(shards) > 1 {
			hasDup = true
			fmt.Fprintf(os.Stderr, "Warning: key %q found in multiple shards %v (inconsistent state)\n", k, shards)
		}
	}
	if hasDup {
		fmt.Fprintln(os.Stderr, "Detected duplicate keys across shards, will deduplicate during migration")
	}

	// Collect misplaced keys (wrong shard) for cleanup even if legacy empty
	misplacedGrouped := make(map[int]map[string]interface{})
	for sid, dataMap := range currentData {
		for k, v := range dataMap {
			correctSid := hashKey(k, cfg.ShardCount)
			if correctSid != sid {
				if _, ok := misplacedGrouped[correctSid]; !ok {
					misplacedGrouped[correctSid] = make(map[string]interface{})
				}
				if _, exists := misplacedGrouped[correctSid][k]; !exists {
					misplacedGrouped[correctSid][k] = v
				}
			}
		}
	}

	// Group legacy keys
	legacyGrouped := make(map[int]map[string]interface{})
	for k, v := range legacyData {
		sid := hashKey(k, cfg.ShardCount)
		if _, ok := legacyGrouped[sid]; !ok {
			legacyGrouped[sid] = make(map[string]interface{})
		}
		legacyGrouped[sid][k] = v
	}

	totalLegacy := len(legacyData)
	if totalLegacy == 0 {
		fmt.Println("Legacy file is empty, nothing to migrate")
		// Still perform cleanup of misplaced/duplicates even if legacy empty
		if len(misplacedGrouped) == 0 && !hasDup {
			if backupPath != "" {
				if err := copyFileIO(legacyPath, backupPath); err != nil {
					fmt.Fprintf(os.Stderr, "Failed to create backup: %v\n", err)
					return err
				}
				fmt.Printf("Backup created at %s\n", backupPath)
			}
			fmt.Printf("Migration completed: %d keys processed (cleanup only)\n", totalLegacy)
			return nil
		}
		fmt.Printf("But found %d misplaced/dup keys to cleanup\n", len(misplacedGrouped))
	}

	fmt.Printf("Migration plan: %d keys from %s -> %d shards\n", totalLegacy, legacyPath, cfg.ShardCount)
	for sid := 0; sid < cfg.ShardCount; sid++ {
		if g, ok := legacyGrouped[sid]; ok {
			fmt.Printf("  shard %d (%s): %d keys\n", sid, idToPath[sid], len(g))
		}
	}
	if dryRun {
		fmt.Println("Dry-run enabled, not writing any shard files")
		if hasDup || len(misplacedGrouped) > 0 {
			fmt.Fprintf(os.Stderr, "Dry-run would also cleanup %d misplaced keys\n", len(misplacedGrouped))
		}
		return nil
	}

	if backupPath != "" {
		if err := os.MkdirAll(filepath.Dir(backupPath), 0755); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to create backup dir: %v\n", err)
			return err
		}
		if err := copyFileIO(legacyPath, backupPath); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to create backup: %v\n", err)
			return err
		}
		fmt.Printf("Backup of legacy created at %s\n", backupPath)
		for _, s := range cfg.Shards {
			if _, err := os.Stat(s.Path); err == nil {
				bak := s.Path + ".bak"
				_ = copyFileIO(s.Path, bak)
			}
		}
	}

	// Build final merged per shard with cleanup
	for sid := 0; sid < cfg.ShardCount; sid++ {
		shardPath, ok := idToPath[sid]
		if !ok {
			continue
		}
		base := make(map[string]interface{})
		for k, v := range currentData[sid] {
			if hashKey(k, cfg.ShardCount) == sid {
				base[k] = v
			}
		}
		initialLen := len(currentData[sid])
		changed := len(base) != initialLen

		if mg, ok := misplacedGrouped[sid]; ok {
			for k, v := range mg {
				if _, exists := base[k]; !exists {
					base[k] = v
					changed = true
				}
			}
		}
		if lg, ok := legacyGrouped[sid]; ok {
			for k, v := range lg {
				if existing, exists := base[k]; !exists {
					base[k] = v
					changed = true
				} else if force {
					if fmt.Sprintf("%v", existing) != fmt.Sprintf("%v", v) {
						fmt.Fprintf(os.Stderr, "Overwriting key '%s' in shard %d\n", k, sid)
					}
					base[k] = v
					changed = true
				}
			}
		}
		if !changed && !force {
			fmt.Printf("Shard %d already up-to-date (%d legacy keys)\n", sid, len(legacyGrouped[sid]))
			continue
		}
		if err := atomicWrite(shardPath, base); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to write shard %d: %v\n", sid, err)
			return err
		}
		fmt.Printf("Migrated %d legacy keys to shard %d (cleanup removed %d)\n", len(legacyGrouped[sid]), sid, initialLen-len(base)+len(legacyGrouped[sid])+len(misplacedGrouped[sid]))
	}
	fmt.Printf("Migration completed: %d keys processed\n", totalLegacy)
	return nil
}

func printHelp() {
	fmt.Println(`Usage:
  proxy --config /app/config.json [--legacy /app/data/legacy.json] <command> [args]

Commands:
  get-shard-id <key>          prints shard id
  get-shard-path <key>        prints shard path
  get <key>                   prints JSON value or null
  set <key> <value_json>      stores JSON value (raw string if not JSON)
  delete <key>                prints true/false
  list-keys                   prints sorted JSON array (union with legacy in turn2)
  distribution                prints JSON map shard_id -> count, includes zeros
  migrate                     migrates legacy to shards
    --dry-run                 print plan, no writes
    --backup <path>           backup legacy and shards as .bak
    --force                   overwrite existing keys, log overwriting

Flags:
  --config string (default /app/config.json)
  --legacy string (default /app/data/legacy.json)
  --help -h help
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
		} else if a == "--help" || a == "-h" || a == "help" {
			printHelp()
			return
		} else {
			filtered = append(filtered, a)
		}
	}

	if len(filtered) == 0 {
		printHelp()
		return
	}

	cmd := filtered[0]
	cmdArgs := filtered[1:]

	if cmd == "migrate" {
		for _, a := range cmdArgs {
			if a == "--help" || a == "-h" || a == "help" {
				printHelp()
				return
			}
		}
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
		fmt.Fprintf(os.Stderr, "Invalid config %s: %v\n", configPath, err)
		os.Exit(2)
	}

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
	default:
		fmt.Fprintf(os.Stderr, "Unknown command %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
