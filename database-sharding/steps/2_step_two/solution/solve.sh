#!/bin/bash
set -e

# Turn2 intermediate hard: weighted + global broadcast + checksum no-escape + versioned files (shard_id+version) + duplicate/wrong-shard cleanup + ops.log replay file order, no staging, no updated_at (harder than loosened but easier than 0/10 extra-hard)

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
	"io"
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
	ShardID  *int                   `json:"shard_id,omitempty"`
	Version  *int                   `json:"version,omitempty"`
	Data     map[string]interface{} `json:"data"`
	Checksum string                 `json:"checksum"`
}

type ShardingProxy struct {
	ConfigPath string
	LegacyPath string
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

func atomicWrite(path string, data map[string]interface{}, shardID int, version int) error {
	if data == nil {
		data = map[string]interface{}{}
	}
	checksum := computeChecksum(data)
	sid := shardID
	ver := version
	sf := ShardFile{ShardID: &sid, Version: &ver, Data: data, Checksum: checksum}
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

func readShardFileWithMeta(path string, expectedShardID int) (map[string]interface{}, int, error) {
	// Returns data, version, error, handles old flat, Turn1 data+checksum, Turn2 versioned
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return map[string]interface{}{}, 0, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]interface{}{}, 0, nil
		}
		return nil, 0, err
	}
	trim := strings.TrimSpace(string(b))
	if trim == "" {
		return map[string]interface{}{}, 0, nil
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(b, &raw); err != nil {
		backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = copyFile(path, backupPath)
		fmt.Fprintf(os.Stderr, "Warning: shard file %s is corrupted (invalid JSON), backup to %s: %v\n", path, backupPath, err)
		_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
		return map[string]interface{}{}, 0, nil
	}
	if _, hasData := raw["data"]; hasData {
		var sf ShardFile
		if err := json.Unmarshal(b, &sf); err != nil {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s corrupted (unmarshal), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, nil
		}
		if sf.Data == nil {
			sf.Data = map[string]interface{}{}
		}
		// If shard_id present, validate it
		if _, hasSid := raw["shard_id"]; hasSid {
			if sf.ShardID == nil {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s missing shard_id (corrupt), backup to %s\n", path, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, nil
			}
			if *sf.ShardID != expectedShardID {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s shard_id mismatch (corrupt), expected %d got %d, backup to %s\n", path, expectedShardID, *sf.ShardID, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, nil
			}
		}
		if _, hasVer := raw["version"]; hasVer {
			if sf.Version == nil || *sf.Version < 0 {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s missing or invalid version (corrupt), backup to %s\n", path, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, nil
			}
		}
		if _, hasCs := raw["checksum"]; !hasCs {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, nil
		}
		if sf.Checksum == "" {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, nil
		}
		expected := computeChecksum(sf.Data)
		if expected != sf.Checksum {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s checksum mismatch (corrupt), expected %s got %s, backup to %s\n", path, expected, sf.Checksum, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, nil
		}
		ver := 0
		if sf.Version != nil {
			ver = *sf.Version
		}
		return sf.Data, ver, nil
	}
	return raw, 0, nil
}

func isGlobalKey(key string) bool {
	return strings.HasPrefix(key, "global:")
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

func hashKeyWeighted(key string, cfg Config) int {
	if isGlobalKey(key) {
		return -1
	}
	totW := totalWeight(cfg)
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

func NewShardingProxyWithLegacy(configPath, legacyPath, opsLogPath string) (*ShardingProxy, error) {
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
			if err := atomicWrite(s.Path, map[string]interface{}{}, s.ID, 0); err != nil {
				return nil, err
			}
		} else {
			_, _, _ = readShardFileWithMeta(s.Path, s.ID)
		}
	}
	if opsLogPath == "" {
		opsLogPath = "/app/data/ops.log"
	}
	if _, err := os.Stat(opsLogPath); os.IsNotExist(err) {
		_ = os.WriteFile(opsLogPath, []byte{}, 0644)
	}
	return &ShardingProxy{ConfigPath: configPath, LegacyPath: legacyPath, OpsLogPath: opsLogPath, Config: cfg}, nil
}

func NewShardingProxy(configPath string) (*ShardingProxy, error) {
	return NewShardingProxyWithLegacy(configPath, "/app/data/legacy.json", "/app/data/ops.log")
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

func (p *ShardingProxy) readLegacy() (map[string]interface{}, error) {
	if _, err := os.Stat(p.LegacyPath); os.IsNotExist(err) {
		return map[string]interface{}{}, nil
	}
	b, err := os.ReadFile(p.LegacyPath)
	if err != nil {
		return map[string]interface{}{}, nil
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
	if isGlobalKey(key) {
		shardsSorted := make([]Shard, len(p.Config.Shards))
		copy(shardsSorted, p.Config.Shards)
		sort.Slice(shardsSorted, func(i, j int) bool { return shardsSorted[i].ID < shardsSorted[j].ID })
		for _, s := range shardsSorted {
			m, _, err := readShardFileWithMeta(s.Path, s.ID)
			if err != nil {
				continue
			}
			if v, ok := m[key]; ok {
				return v, true
			}
		}
		leg, _ := p.readLegacy()
		if v, ok := leg[key]; ok {
			return v, true
		}
		return nil, false
	}
	sid := p.GetShardID(key)
	path, err := p.GetShardPath(key)
	if err != nil {
		return nil, false
	}
	m, _, err := readShardFileWithMeta(path, sid)
	if err != nil {
		return nil, false
	}
	if v, ok := m[key]; ok {
		return v, true
	}
	leg, _ := p.readLegacy()
	v2, ok2 := leg[key]
	return v2, ok2
}

func (p *ShardingProxy) Set(key string, value interface{}) error {
	if isGlobalKey(key) {
		for _, s := range p.Config.Shards {
			m, ver, err := readShardFileWithMeta(s.Path, s.ID)
			if err != nil {
				return err
			}
			m[key] = value
			if err := atomicWrite(s.Path, m, s.ID, ver+1); err != nil {
				return err
			}
		}
		_ = appendOpsLog(p.OpsLogPath, "set", key, value, -1)
		return nil
	}
	sid := p.GetShardID(key)
	path, err := p.GetShardPath(key)
	if err != nil {
		return err
	}
	m, ver, err := readShardFileWithMeta(path, sid)
	if err != nil {
		return err
	}
	m[key] = value
	if err := atomicWrite(path, m, sid, ver+1); err != nil {
		return err
	}
	_ = appendOpsLog(p.OpsLogPath, "set", key, value, sid)
	return nil
}

func (p *ShardingProxy) Delete(key string) (bool, error) {
	if isGlobalKey(key) {
		deleted := false
		for _, s := range p.Config.Shards {
			m, ver, err := readShardFileWithMeta(s.Path, s.ID)
			if err != nil {
				return false, err
			}
			if _, ok := m[key]; ok {
				delete(m, key)
				if err := atomicWrite(s.Path, m, s.ID, ver+1); err != nil {
					return false, err
				}
				deleted = true
			}
		}
		if deleted {
			_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, -1)
		}
		return deleted, nil
	}
	sid := p.GetShardID(key)
	path, err := p.GetShardPath(key)
	if err != nil {
		return false, err
	}
	m, ver, err := readShardFileWithMeta(path, sid)
	if err != nil {
		return false, err
	}
	if _, ok := m[key]; ok {
		delete(m, key)
		if err := atomicWrite(path, m, sid, ver+1); err != nil {
			return false, err
		}
		_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, sid)
		return true, nil
	}
	return false, nil
}

func (p *ShardingProxy) GetAllKeys() ([]string, error) {
	keysMap := make(map[string]struct{})
	for _, s := range p.Config.Shards {
		m, _, err := readShardFileWithMeta(s.Path, s.ID)
		if err != nil {
			return nil, err
		}
		for k := range m {
			keysMap[k] = struct{}{}
		}
	}
	leg, _ := p.readLegacy()
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
		m, _, err := readShardFileWithMeta(s.Path, s.ID)
		if err != nil {
			return nil, err
		}
		dist[s.ID] = len(m)
	}
	return dist, nil
}

func appendOpsLog(logPath, op, key string, value interface{}, shardID int) error {
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
	}
	if op == "set" {
		entry["value"] = value
	}
	enc := json.NewEncoder(f)
	enc.SetEscapeHTML(false)
	return enc.Encode(entry)
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

func migrate(legacyPath, configPath, opsLogPath string, dryRun bool, backupPath string, force bool) error {
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

	opsEntries, err := readOpsLog(opsLogPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to read ops.log %s: %v\n", opsLogPath, err)
		opsEntries = []map[string]interface{}{}
	}

	currentData := make(map[int]map[string]interface{})
	currentVersions := make(map[int]int)
	keyToShards := make(map[string][]int)
	for _, s := range cfg.Shards {
		m, ver, err := readShardFileWithMeta(s.Path, s.ID)
		if err != nil {
			m = map[string]interface{}{}
			ver = 0
		}
		currentData[s.ID] = m
		currentVersions[s.ID] = ver
		for k := range m {
			if !isGlobalKey(k) {
				keyToShards[k] = append(keyToShards[k], s.ID)
			}
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

	misplacedGrouped := make(map[int]map[string]interface{})
	for sid, dataMap := range currentData {
		for k, v := range dataMap {
			if isGlobalKey(k) {
				continue
			}
			correctSid := hashKeyWeighted(k, cfg)
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

	legacyGrouped := make(map[int]map[string]interface{})
	for k, v := range legacyData {
		if isGlobalKey(k) {
			for sid := 0; sid < cfg.ShardCount; sid++ {
				if _, ok := legacyGrouped[sid]; !ok {
					legacyGrouped[sid] = make(map[string]interface{})
				}
				legacyGrouped[sid][k] = v
			}
		} else {
			sid := hashKeyWeighted(k, cfg)
			if sid == -1 {
				continue
			}
			if _, ok := legacyGrouped[sid]; !ok {
				legacyGrouped[sid] = make(map[string]interface{})
			}
			legacyGrouped[sid][k] = v
		}
	}

	totalLegacy := len(legacyData)
	if totalLegacy == 0 {
		fmt.Println("Legacy file is empty, nothing to migrate")
		if len(misplacedGrouped) == 0 && !hasDup && len(opsEntries) == 0 {
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
		fmt.Printf("But found %d misplaced/dup keys and %d ops log entries to cleanup/replay\n", len(misplacedGrouped), len(opsEntries))
	}

	fmt.Printf("Migration plan: %d keys from %s -> %d shards\n", totalLegacy, legacyPath, cfg.ShardCount)
	for sid := 0; sid < cfg.ShardCount; sid++ {
		if g, ok := legacyGrouped[sid]; ok && len(g) > 0 {
			fmt.Printf("  shard %d (%s): %d legacy keys\n", sid, idToPath[sid], len(g))
		}
	}
	if dryRun {
		fmt.Println("Dry-run enabled, not writing any shard files")
		if hasDup || len(misplacedGrouped) > 0 {
			fmt.Fprintf(os.Stderr, "Dry-run would also cleanup %d misplaced keys and %d duplicate groups\n", len(misplacedGrouped), len(keyToShards))
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
		if _, err := os.Stat(opsLogPath); err == nil {
			_ = copyFileIO(opsLogPath, opsLogPath+".bak")
		}
	}

	for sid := 0; sid < cfg.ShardCount; sid++ {
		shardPath, ok := idToPath[sid]
		if !ok {
			continue
		}
		base := make(map[string]interface{})
		for k, v := range currentData[sid] {
			if isGlobalKey(k) {
				base[k] = v
				continue
			}
			if hashKeyWeighted(k, cfg) == sid {
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
				if _, exists := base[k]; !exists {
					base[k] = v
					changed = true
				} else if force {
					if fmt.Sprintf("%v", base[k]) != fmt.Sprintf("%v", v) {
						fmt.Fprintf(os.Stderr, "Overwriting key '%s' in shard %d\n", k, sid)
					}
					base[k] = v
					changed = true
				}
			}
		}
		if !changed && !force && len(opsEntries) == 0 {
			fmt.Printf("Shard %d already up-to-date (%d legacy keys)\n", sid, len(legacyGrouped[sid]))
			continue
		}
		if err := atomicWrite(shardPath, base, sid, currentVersions[sid]+1); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to write shard %d: %v\n", sid, err)
			return err
		}
		if changed || len(legacyGrouped[sid]) > 0 {
			fmt.Printf("Migrated %d legacy keys to shard %d\n", len(legacyGrouped[sid]), sid)
		}
	}

	for _, entry := range opsEntries {
		op, _ := entry["op"].(string)
		key, _ := entry["key"].(string)
		if op == "" || key == "" {
			continue
		}
		if op == "set" {
			val, ok := entry["value"]
			if !ok {
				continue
			}
			if isGlobalKey(key) {
				for _, s := range cfg.Shards {
					m, ver, _ := readShardFileWithMeta(s.Path, s.ID)
					m[key] = val
					_ = atomicWrite(s.Path, m, s.ID, ver+1)
				}
			} else {
				sid := hashKeyWeighted(key, cfg)
				if sid == -1 {
					continue
				}
				path := idToPath[sid]
				m, ver, _ := readShardFileWithMeta(path, sid)
				m[key] = val
				_ = atomicWrite(path, m, sid, ver+1)
			}
		} else if op == "delete" {
			if isGlobalKey(key) {
				for _, s := range cfg.Shards {
					m, ver, _ := readShardFileWithMeta(s.Path, s.ID)
					if _, ok := m[key]; ok {
						delete(m, key)
						_ = atomicWrite(s.Path, m, s.ID, ver+1)
					}
				}
			} else {
				sid := hashKeyWeighted(key, cfg)
				path := idToPath[sid]
				m, ver, _ := readShardFileWithMeta(path, sid)
				if _, ok := m[key]; ok {
					delete(m, key)
					_ = atomicWrite(path, m, sid, ver+1)
				}
			}
		}
	}
	if len(opsEntries) > 0 {
		fmt.Printf("Ops log replay completed: %d entries\n", len(opsEntries))
	}

	globalKeys := make(map[string]interface{})
	for _, s := range cfg.Shards {
		m, _, _ := readShardFileWithMeta(s.Path, s.ID)
		for k, v := range m {
			if isGlobalKey(k) {
				if _, ok := globalKeys[k]; !ok {
					globalKeys[k] = v
				}
			}
		}
	}
	for k, v := range globalKeys {
		for _, s := range cfg.Shards {
			m, ver, _ := readShardFileWithMeta(s.Path, s.ID)
			if _, ok := m[k]; !ok {
				m[k] = v
				_ = atomicWrite(s.Path, m, s.ID, ver+1)
			}
		}
	}

	fmt.Printf("Migration completed: %d keys processed\n", totalLegacy)
	return nil
}

func printHelp() {
	fmt.Println(`Usage: proxy --config /app/config.json [--legacy /app/data/legacy.json] [--ops-log /app/data/ops.log] <command> [args]
Commands:
  get-shard-id <key>          -1 for global: broadcast
  get-shard-path <key>        comma-separated for global:
  get <key>                   JSON value or null, fallback to legacy
  set <key> <value_json>      set, raw string if not JSON, replicates if global:, appends ops.log
  delete <key>                true/false, deletes from all if global:
  list-keys                   sorted union of shards + legacy
  distribution                JSON map shard_id->count including zeros
  ops-log                     prints ops.log JSON array, skips corrupt
  migrate [--dry-run] [--backup path] [--force]  migrates legacy, cleans duplicates, replays ops.log
Flags:
  --config string (default /app/config.json)
  --legacy string (default /app/data/legacy.json)
  --ops-log string (default /app/data/ops.log)
  --help -h help
  weight per shard via config weight field, default 1, must be >0 if present
  global: prefix for broadcast
  ops.log at /app/data/ops.log
  checksum integrity without HTML escaping
  dry-run, backup, force, ops.log replay
`)
}

func main() {
	configPath := "/app/config.json"
	legacyPath := "/app/data/legacy.json"
	opsLogPath := "/app/data/ops.log"
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
		} else if a == "--ops-log" && i+1 < len(args) {
			opsLogPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--ops-log=") {
			opsLogPath = strings.TrimPrefix(a, "--ops-log=")
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
			if strings.HasPrefix(a, "--") {
				if a == "--dry-run" || a == "--force" {
				} else if strings.HasPrefix(a, "--backup=") {
				} else if a == "--backup" {
					if i+1 < len(cmdArgs) {
						i++
					}
				} else if strings.HasPrefix(a, "--config") || strings.HasPrefix(a, "--legacy") || strings.HasPrefix(a, "--ops-log") {
					if a == "--config" || a == "--legacy" || a == "--ops-log" {
						if i+1 < len(cmdArgs) {
							i++
						}
					}
				} else {
					fmt.Fprintf(os.Stderr, "Unknown migrate flag: %s\n", a)
					os.Exit(2)
				}
			} else {
				fmt.Fprintf(os.Stderr, "Unknown migrate arg: %s\n", a)
				os.Exit(2)
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
		if err := migrate(legacyPath, configPath, opsLogPath, dryRun, backupPath, force); err != nil {
			lower := strings.ToLower(err.Error())
			if strings.Contains(lower, "config") || strings.Contains(lower, "shard") || strings.Contains(lower, "duplicate") || strings.Contains(lower, "weight") || strings.Contains(lower, "negative") || strings.Contains(lower, "empty") {
				os.Exit(2)
			}
			os.Exit(1)
		}
		return
	}

	proxy, err := NewShardingProxyWithLegacy(configPath, legacyPath, opsLogPath)
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
		if cmdArgs[0] == "" {
			fmt.Fprintln(os.Stderr, "empty key not allowed")
			os.Exit(2)
		}
		fmt.Println(proxy.GetShardID(cmdArgs[0]))
	case "get-shard-path":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires <key>")
			os.Exit(2)
		}
		if cmdArgs[0] == "" {
			fmt.Fprintln(os.Stderr, "empty key not allowed")
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
		if cmdArgs[0] == "" {
			fmt.Fprintln(os.Stderr, "empty key not allowed")
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
		if cmdArgs[0] == "" {
			fmt.Fprintln(os.Stderr, "empty key not allowed")
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
		if cmdArgs[0] == "" {
			fmt.Fprintln(os.Stderr, "empty key not allowed")
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
