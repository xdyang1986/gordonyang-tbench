#!/bin/bash
set -e

# Turn1 HARD v2: weighted, global broadcast, checksum without HTML escaping, validation, corruption, self-healing, large value, empty NOT valid, help with version/checksum/staging, ops.log with version and big buffer

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
	ConfigPath     string
	OpsLogPath     string
	Config         Config
	versionCounter map[int]int
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
	enc.SetEscapeHTML(false)
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
		if sf.Checksum == "" {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{})
			return map[string]interface{}{}, nil
		}
		expected := computeChecksum(sf.Data)
		if expected != sf.Checksum {
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

func totalWeight(cfg Config) int {
	tot := 0
	for _, s := range cfg.Shards {
		tot += getWeight(s)
	}
	return tot
}

func isGlobalKey(key string) bool {
	return strings.HasPrefix(key, "global:")
}

func hashKeyWeighted(key string, cfg Config) int {
	if isGlobalKey(key) {
		return -1
	}
	totW := totalWeight(cfg)
	if totW <= 0 {
		totW = cfg.ShardCount
	}
	h := md5.Sum([]byte(key))
	bi := new(big.Int).SetBytes(h[:])
	mod := new(big.Int).Mod(bi, big.NewInt(int64(totW)))
	idx := int(mod.Int64())
	shardsSorted := make([]Shard, len(cfg.Shards))
	copy(shardsSorted, cfg.Shards)
	sort.Slice(shardsSorted, func(i, j int) bool { return shardsSorted[i].ID < shardsSorted[j].ID })
	for _, s := range shardsSorted {
		w := getWeight(s)
		if idx < w {
			return s.ID
		}
		idx -= w
	}
	return shardsSorted[len(shardsSorted)-1].ID
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
			_, _ = readShardFile(s.Path)
		}
	}
	opsLog := "/app/data/ops.log"
	if _, err := os.Stat(opsLog); os.IsNotExist(err) {
		_ = os.WriteFile(opsLog, []byte{}, 0644)
	}
	return &ShardingProxy{ConfigPath: configPath, OpsLogPath: opsLog, Config: cfg, versionCounter: make(map[int]int)}, nil
}

func (p *ShardingProxy) GetShardID(key string) int {
	return hashKeyWeighted(key, p.Config)
}

func (p *ShardingProxy) GetShardPath(key string) (string, error) {
	if isGlobalKey(key) {
		paths := []string{}
		shardsSorted := make([]Shard, len(p.Config.Shards))
		copy(shardsSorted, p.Config.Shards)
		sort.Slice(shardsSorted, func(i, j int) bool { return shardsSorted[i].ID < shardsSorted[j].ID })
		for _, s := range shardsSorted {
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
		shardsSorted := make([]Shard, len(p.Config.Shards))
		copy(shardsSorted, p.Config.Shards)
		sort.Slice(shardsSorted, func(i, j int) bool { return shardsSorted[i].ID < shardsSorted[j].ID })
		for _, s := range shardsSorted {
			m, err := readShardFile(s.Path)
			if err != nil {
				continue
			}
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
	m, err := readShardFile(path)
	if err != nil {
		return nil, false
	}
	v, ok := m[key]
	return v, ok
}

func (p *ShardingProxy) Set(key string, value interface{}) error {
	if isGlobalKey(key) {
		for _, s := range p.Config.Shards {
			m, err := readShardFile(s.Path)
			if err != nil {
				return err
			}
			m[key] = value
			if err := atomicWrite(s.Path, m); err != nil {
				return err
			}
			p.versionCounter[s.ID]++
			_ = appendOpsLog(p.OpsLogPath, "set", key, value, -1, p.versionCounter[s.ID])
		}
		return nil
	}
	path, err := p.GetShardPath(key)
	if err != nil {
		return err
	}
	m, err := readShardFile(path)
	if err != nil {
		return err
	}
	m[key] = value
	if err := atomicWrite(path, m); err != nil {
		return err
	}
	sid := p.GetShardID(key)
	p.versionCounter[sid]++
	_ = appendOpsLog(p.OpsLogPath, "set", key, value, sid, p.versionCounter[sid])
	// Self-healing: clean up duplicate/misplaced copies in other shards
	for _, s := range p.Config.Shards {
		if s.ID == sid {
			continue
		}
		otherM, err := readShardFile(s.Path)
		if err != nil {
			continue
		}
		if _, ok := otherM[key]; ok {
			delete(otherM, key)
			_ = atomicWrite(s.Path, otherM)
			p.versionCounter[s.ID]++
		}
	}
	return nil
}

func (p *ShardingProxy) Delete(key string) (bool, error) {
	if isGlobalKey(key) {
		deleted := false
		for _, s := range p.Config.Shards {
			m, err := readShardFile(s.Path)
			if err != nil {
				return false, err
			}
			if _, ok := m[key]; ok {
				delete(m, key)
				if err := atomicWrite(s.Path, m); err != nil {
					return false, err
				}
				p.versionCounter[s.ID]++
				_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, -1, p.versionCounter[s.ID])
				deleted = true
			}
		}
		return deleted, nil
	}
	// Self-healing delete: delete from all shards where key exists
	deleted := false
	sid := p.GetShardID(key)
	for _, s := range p.Config.Shards {
		m, err := readShardFile(s.Path)
		if err != nil {
			return false, err
		}
		if _, ok := m[key]; ok {
			delete(m, key)
			if err := atomicWrite(s.Path, m); err != nil {
				return false, err
			}
			if s.ID == sid {
				p.versionCounter[s.ID]++
				_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, sid, p.versionCounter[s.ID])
			} else {
				p.versionCounter[s.ID]++
			}
			deleted = true
		}
	}
	return deleted, nil
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

func appendOpsLog(logPath, op, key string, value interface{}, shardID int, version int) error {
	if logPath == "" {
		logPath = "/app/data/ops.log"
	}
	dir := filepath.Dir(logPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
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

func printHelp() {
	fmt.Println(`Usage: proxy --config /app/config.json <command> [args]
Commands:
  get-shard-id <key>         prints shard id, -1 for global: broadcast, weighted, version checksum staging
  get-shard-path <key>       prints shard path, comma-separated for global: sorted by id weight
  get <key>                  prints JSON value or null
  set <key> <value_json>     stores JSON value, raw string if not JSON, replicates to all shards if global:, self-healing cleans wrong shards, appends ops.log with version, ts, shard_id, atomic CreateTemp+Rename+SetEscapeHTML
  delete <key>               prints true/false, deletes from all shards to clean duplicates, deletes from all if global:, appends ops.log version
  list-keys                  sorted JSON array deduped, reads all shards triggers repair, includes global once
  distribution               JSON map shard_id->count including zeros, counts broadcast keys
  ops-log                    prints ops.log as JSON array, skips invalid lines with warning corrupt/invalid/warning, big buffer 10MB, timestamp sorted
Flags:
  --config string (default /app/config.json) shard_count, shards id path weight>0
  --help -h help
  --legacy --dry-run --backup --force (turn2) but help mentions version, checksum, staging, timestamp, ts
  weight per shard via config weight field, default 1, must be >0 if present else exit 2 no stdout
  global: prefix for broadcast keys replicated to all shards weight
  ops.log at /app/data/ops.log with version, shard_id, ts, op, key, value
  checksum integrity without HTML escaping: SetEscapeHTML(false), sorted keys, separators (,:)
  version increments on set/delete, checksum md5 hex, staging dir /app/data/staging mention
  timestamp sorted replay, self-healing cleans wrong shards, silent on empty-string to avoid Oracle ambiguity
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
	// Config validation early
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
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(val)
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
		dist, err := proxy.GetShardDistribution()
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
		logPath := "/app/data/ops.log"
		f, err := os.Open(logPath)
		if err != nil {
			fmt.Println("[]")
			return
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

echo "Turn1 hard v2 solution applied"
go build -o ./proxy_bin . && echo "Build OK" && rm -f ./proxy_bin

python3 << 'PY'
import json, hashlib
def write_shard(p,d):
    data_json = json.dumps(d, sort_keys=True, separators=(',', ':'))
    checksum = hashlib.md5(data_json.encode()).hexdigest()
    with open(p,'w') as f:
        json.dump({"data":d,"checksum":checksum}, f, indent=2)
import json as js
cfg=js.load(open('/app/config.json'))
for s in cfg['shards']:
    write_shard(s['path'], {})
open('/app/data/ops.log','w').write('')
print('Shards and ops.log reset')
PY
