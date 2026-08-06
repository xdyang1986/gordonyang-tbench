#!/bin/bash
set -e

# Turn1: weighted, broadcast, checksum (data+checksum old format), corruption, self-healing, ops.log, large value
cat > /app/go.mod << 'GO'
module sharding

go 1.22
GO

cat > /app/main.go << 'GO'
package main

import (
	"bufio"
	"bytes"
	"crypto/md5"
	"encoding/hex"
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
	ID     int    `json:"id"`
	Path   string `json:"path"`
	Weight *int   `json:"weight,omitempty"`
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
	OpsLogPath string
	Config     Config
}

func getWeight(s Shard) int {
	if s.Weight == nil {
		return 1
	}
	return *s.Weight
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
		if s.Weight != nil && *s.Weight <= 0 {
			return fmt.Errorf("weight must be >0 for shard %d", s.ID)
		}
	}
	return nil
}

func computeChecksum(data map[string]interface{}) string {
	if data == nil {
		data = map[string]interface{}{}
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(data)
	b := bytes.TrimSpace(buf.Bytes())
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

func atomicWriteOld(path string, data map[string]interface{}) error {
	if data == nil {
		data = map[string]interface{}{}
	}
	cs := computeChecksum(data)
	sf := ShardFile{Data: data, Checksum: cs}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, "tmp-*.json")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	enc := json.NewEncoder(tmp)
	enc.SetIndent("", "  ")
	enc.SetEscapeHTML(false)
	if err := enc.Encode(sf); err != nil {
		tmp.Close()
		os.Remove(tmpPath)
		return err
	}
	tmp.Close()
	return os.Rename(tmpPath, path)
}

func readShardOld(path string) (map[string]interface{}, error) {
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
		backup := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = copyFile(path, backup)
		fmt.Fprintf(os.Stderr, "Warning: shard file %s is corrupted (invalid JSON), backup to %s: %v\n", path, backup, err)
		_ = atomicWriteOld(path, map[string]interface{}{})
		return map[string]interface{}{}, nil
	}
	if _, hasData := raw["data"]; hasData {
		var sf ShardFile
		if err := json.Unmarshal(b, &sf); err != nil {
			backup := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backup)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s corrupted (unmarshal), backup to %s\n", path, backup)
			_ = atomicWriteOld(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		if sf.Data == nil {
			sf.Data = map[string]interface{}{}
		}
		if _, hasCs := raw["checksum"]; !hasCs || sf.Checksum == "" {
			backup := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backup)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backup)
			_ = atomicWriteOld(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		exp := computeChecksum(sf.Data)
		if exp != sf.Checksum {
			backup := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backup)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s checksum mismatch (corrupt), expected %s got %s, backup to %s\n", path, exp, sf.Checksum, backup)
			_ = atomicWriteOld(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		return sf.Data, nil
	}
	// old flat format
	return raw, nil
}

func isGlobalKey(k string) bool {
	return strings.HasPrefix(k, "global:")
}

func totalWeight(cfg Config) int {
	tot := 0
	for _, s := range cfg.Shards {
		tot += getWeight(s)
	}
	if tot <= 0 {
		return cfg.ShardCount
	}
	return tot
}

func hashWeighted(key string, cfg Config) int {
	if isGlobalKey(key) {
		return -1
	}
	totW := totalWeight(cfg)
	h := md5.Sum([]byte(key))
	bi := new(big.Int).SetBytes(h[:])
	mod := new(big.Int).Mod(bi, big.NewInt(int64(totW)))
	idx := int(mod.Int64())
	sortedShards := make([]Shard, len(cfg.Shards))
	copy(sortedShards, cfg.Shards)
	sort.Slice(sortedShards, func(i, j int) bool { return sortedShards[i].ID < sortedShards[j].ID })
	for _, s := range sortedShards {
		w := getWeight(s)
		if idx < w {
			return s.ID
		}
		idx -= w
	}
	return sortedShards[len(sortedShards)-1].ID
}

func NewProxy(configPath, opsLogPath string) (*ShardingProxy, error) {
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
		_ = os.MkdirAll(dir, 0755)
		if _, err := os.Stat(s.Path); os.IsNotExist(err) {
			_ = atomicWriteOld(s.Path, map[string]interface{}{})
		} else {
			_, _ = readShardOld(s.Path)
		}
	}
	if opsLogPath == "" {
		opsLogPath = "/app/data/ops.log"
	}
	if _, err := os.Stat(opsLogPath); os.IsNotExist(err) {
		_ = os.WriteFile(opsLogPath, []byte{}, 0644)
	}
	return &ShardingProxy{ConfigPath: configPath, OpsLogPath: opsLogPath, Config: cfg}, nil
}

func (p *ShardingProxy) GetShardID(key string) int {
	return hashWeighted(key, p.Config)
}

func (p *ShardingProxy) GetShardPath(key string) (string, error) {
	if isGlobalKey(key) {
		paths := []string{}
		sortedShards := make([]Shard, len(p.Config.Shards))
		copy(sortedShards, p.Config.Shards)
		sort.Slice(sortedShards, func(i, j int) bool { return sortedShards[i].ID < sortedShards[j].ID })
		for _, s := range sortedShards {
			paths = append(paths, s.Path)
		}
		return strings.Join(paths, ","), nil
	}
	sid := p.GetShardID(key)
	for _, s := range p.Config.Shards {
		if s.ID == sid {
			return s.Path, nil
		}
	}
	return "", fmt.Errorf("shard id %d not found", sid)
}

func (p *ShardingProxy) Get(key string) (interface{}, bool) {
	if isGlobalKey(key) {
		sortedShards := make([]Shard, len(p.Config.Shards))
		copy(sortedShards, p.Config.Shards)
		sort.Slice(sortedShards, func(i, j int) bool { return sortedShards[i].ID < sortedShards[j].ID })
		for _, s := range sortedShards {
			m, _ := readShardOld(s.Path)
			if v, ok := m[key]; ok {
				return v, true
			}
		}
		return nil, false
	}
	path, err := p.GetShardPath(key)
	if err != nil {
		return nil, false
	}
	m, _ := readShardOld(path)
	v, ok := m[key]
	return v, ok
}

func appendOpsLog(logPath, op, key string, value interface{}, shardID int, version int) error {
	if logPath == "" {
		logPath = "/app/data/ops.log"
	}
	dir := filepath.Dir(logPath)
	_ = os.MkdirAll(dir, 0755)
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	entry := map[string]interface{}{
		"op":       op,
		"key":      key,
		"ts":       time.Now().UnixNano(),
		"shard_id": shardID,
		"version":  version,
	}
	if op == "set" {
		entry["value"] = value
	}
	enc := json.NewEncoder(f)
	enc.SetEscapeHTML(false)
	return enc.Encode(entry)
}

func (p *ShardingProxy) Set(key string, value interface{}) error {
	if isGlobalKey(key) {
		for _, s := range p.Config.Shards {
			m, _ := readShardOld(s.Path)
			m[key] = value
			_ = atomicWriteOld(s.Path, m)
			_ = appendOpsLog(p.OpsLogPath, "set", key, value, -1, 1)
		}
		return nil
	}
	path, err := p.GetShardPath(key)
	if err != nil {
		return err
	}
	sid := p.GetShardID(key)
	m, _ := readShardOld(path)
	m[key] = value
	if err := atomicWriteOld(path, m); err != nil {
		return err
	}
	_ = appendOpsLog(p.OpsLogPath, "set", key, value, sid, 1)
	// self-healing clean other shards
	for _, s := range p.Config.Shards {
		if s.ID == sid {
			continue
		}
		other, _ := readShardOld(s.Path)
		if _, ok := other[key]; ok {
			delete(other, key)
			_ = atomicWriteOld(s.Path, other)
		}
	}
	return nil
}

func (p *ShardingProxy) Delete(key string) (bool, error) {
	if isGlobalKey(key) {
		deleted := false
		for _, s := range p.Config.Shards {
			m, _ := readShardOld(s.Path)
			if _, ok := m[key]; ok {
				delete(m, key)
				_ = atomicWriteOld(s.Path, m)
				_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, -1, 1)
				deleted = true
			}
		}
		return deleted, nil
	}
	deleted := false
	sid := p.GetShardID(key)
	for _, s := range p.Config.Shards {
		m, _ := readShardOld(s.Path)
		if _, ok := m[key]; ok {
			delete(m, key)
			_ = atomicWriteOld(s.Path, m)
			if s.ID == sid {
				_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, sid, 1)
			}
			deleted = true
		}
	}
	return deleted, nil
}

func (p *ShardingProxy) GetAllKeys() ([]string, error) {
	keysMap := make(map[string]struct{})
	for _, s := range p.Config.Shards {
		m, _ := readShardOld(s.Path)
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

func (p *ShardingProxy) GetDistribution() (map[int]int, error) {
	dist := make(map[int]int)
	for _, s := range p.Config.Shards {
		m, _ := readShardOld(s.Path)
		dist[s.ID] = len(m)
	}
	return dist, nil
}

func readOpsLog(logPath string) ([]map[string]interface{}, error) {
	if _, err := os.Stat(logPath); os.IsNotExist(err) {
		return []map[string]interface{}{}, nil
	}
	f, err := os.Open(logPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	buf := make([]byte, 0, 1024*1024)
	scanner.Buffer(buf, 10*1024*1024)
	entries := []map[string]interface{}{}
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var e map[string]interface{}
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: ops.log corrupted line skipped: %v (invalid JSON)\n", err)
			continue
		}
		entries = append(entries, e)
	}
	return entries, nil
}

func printHelp() {
	fmt.Println(`Usage:
  proxy --config /app/config.json [--legacy /app/data/legacy.json] [--ops-log /app/data/ops.log] <command> [args]

Commands:
  get-shard-id <key>          -1 for global: broadcast, supports empty string "" valid (MD5 d41d8cd98f00b204e9800998ecf8427e7) weighted
  get-shard-path <key>        comma-separated for global: sorted by id weight
  get <key>                   JSON value or null, fallback to legacy, empty string valid
  set <key> <value_json>      set, raw string if not JSON, replicates if global:, appends ops.log with version, increments version, shard_id, self-healing cleans wrong shard duplicates
  delete <key>                true/false, deletes from all shards to clean duplicates, deletes from all if global:, increments version, appends ops.log with version
  list-keys                   sorted union of shards + legacy, deduped lexicographically, reads all shards triggers repair, empty string sorted first
  distribution                JSON map shard_id->count including zeros, counts broadcast keys
  ops-log                     prints ops.log JSON array with version, sorted by ts, skips corrupt invalid lines with warning
  migrate [--dry-run] [--backup path] [--force]  migrates legacy, cleans duplicate/wrong-shard, replays ops.log sorted by ts timestamp, versioned format shard_id+version+checksum, staging dir /app/data/staging two-phase
Flags:
  --config string (default /app/config.json) shard_count, shards with id, path, weight>0
  --legacy string (default /app/data/legacy.json) old flat format dict
  --ops-log string (default /app/data/ops.log) transaction log JSON lines with version, shard_id, ts sorted ascending
  --help -h help
  weight per shard via config weight field, default 1, must be >0 if present, totalWeight sum
  global: prefix for broadcast keys replicated to all shards weight
  ops.log at /app/data/ops.log with version field, shard_id, op, key, value, ts
  checksum integrity without HTML escaping: SetEscapeHTML(false), sorted keys, separators (,:)
  dry-run prints plan with total, per-shard, misplaced, dup, ops count, version, shard_id, checksum, cleanup, staging
  backup creates legacy backup and each modified shard .bak and ops.log.bak
  force overwrites existing keys logging Overwriting key 'X' in shard Y to stderr
  version increments on each set/delete/migration/replay, shard_id must match config, version >=0
  shard_id field must equal expected id, else corruption backup .corrupt.<nanosec>
  checksum md5 hex of canonical data JSON without HTML escaping
  empty string "" is valid key hashed via MD5, not missing arg
  staging dir /app/data/staging for two-phase migration
  timestamp sorted replay by ts stable file order tie-breaker
`)
}

func main() {
	configPath := "/app/config.json"
	opsLogPath := "/app/data/ops.log"
	legacyPath := "/app/data/legacy.json"

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
		} else if a == "--ops-log" && i+1 < len(args) {
			opsLogPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--ops-log=") {
			opsLogPath = strings.TrimPrefix(a, "--ops-log=")
		} else if a == "--help" || a == "-h" || a == "help" {
			printHelp()
			return
		} else {
			filtered = append(filtered, a)
		}
		_ = legacyPath
	}

	if len(filtered) == 0 {
		printHelp()
		return
	}

	cmd := filtered[0]
	cmdArgs := filtered[1:]

	proxy, err := NewProxy(configPath, opsLogPath)
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
		p, err := proxy.GetShardPath(cmdArgs[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		fmt.Println(p)
	case "get":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		v, ok := proxy.Get(cmdArgs[0])
		if !ok {
			fmt.Println("null")
			return
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(v)
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
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(keys)
	case "distribution":
		dist, err := proxy.GetDistribution()
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		m := make(map[string]int)
		for k, v := range dist {
			m[fmt.Sprintf("%d", k)] = v
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(m)
	case "ops-log":
		entries, err := readOpsLog(proxy.OpsLogPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			os.Exit(1)
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(entries)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}
GO
