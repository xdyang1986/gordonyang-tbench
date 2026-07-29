#!/bin/bash
set -e

# Hard Turn2 reference solution: weighted, global broadcast, checksum no-escape, version+shard_id, updated_at conflict, tombstone via ops.log replay, staging atomic, duplicate cleanup

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

type ShardFileOld struct {
	Data     map[string]interface{} `json:"data"`
	Checksum string                 `json:"checksum"`
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

// atomicWriteOld writes Turn1 format (data+checksum) for backward compat helper
func atomicWriteOld(path string, data map[string]interface{}) error {
	if data == nil {
		data = map[string]interface{}{}
	}
	checksum := computeChecksum(data)
	sf := ShardFileOld{Data: data, Checksum: checksum}
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

// atomicWrite writes new Turn2 format with shard_id, version, data, checksum
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

func readShardFileWithMeta(path string, expectedShardID int) (map[string]interface{}, int, int, error) {
	// Returns data, version, shardID, error
	// Handles: missing/empty, old flat, Turn1 format (data+checksum), Turn2 format (shard_id,version,data,checksum)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return map[string]interface{}{}, 0, expectedShardID, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]interface{}{}, 0, expectedShardID, nil
		}
		return nil, 0, expectedShardID, err
	}
	trim := strings.TrimSpace(string(b))
	if trim == "" {
		return map[string]interface{}{}, 0, expectedShardID, nil
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(b, &raw); err != nil {
		backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = copyFile(path, backupPath)
		fmt.Fprintf(os.Stderr, "Warning: shard file %s is corrupted (invalid JSON), backup to %s: %v\n", path, backupPath, err)
		_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
		return map[string]interface{}{}, 0, expectedShardID, nil
	}
	// Has data field?
	if _, hasData := raw["data"]; hasData {
		// Try new format with shard_id, version
		var sf ShardFile
		if err := json.Unmarshal(b, &sf); err != nil {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s corrupted (unmarshal), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, expectedShardID, nil
		}
		if sf.Data == nil {
			sf.Data = map[string]interface{}{}
		}
		// If has shard_id field, validate it
		if _, hasSid := raw["shard_id"]; hasSid {
			if sf.ShardID == nil {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s missing shard_id (corrupt), backup to %s\n", path, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, expectedShardID, nil
			}
			if *sf.ShardID != expectedShardID {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s shard_id mismatch (corrupt), expected %d got %d, backup to %s\n", path, expectedShardID, *sf.ShardID, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, expectedShardID, nil
			}
		}
		// If has version field, validate it, else if has shard_id but no version, treat as corruption (per feedback, new format requires version)
		if _, hasVer := raw["version"]; hasVer {
			if sf.Version == nil {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s missing version (corrupt), backup to %s\n", path, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, expectedShardID, nil
			}
			if *sf.Version < 0 {
				backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
				_ = copyFile(path, backupPath)
				fmt.Fprintf(os.Stderr, "Warning: shard file %s version negative (corrupt), backup to %s\n", path, backupPath)
				_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
				return map[string]interface{}{}, 0, expectedShardID, nil
			}
		}
		// Checksum: if data field present, checksum missing or empty => corruption (per feedback)
		if _, hasCs := raw["checksum"]; !hasCs {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, expectedShardID, nil
		}
		var csRaw string
		if csVal, ok := raw["checksum"]; ok {
			if csStr, ok := csVal.(string); ok {
				csRaw = csStr
			}
		}
		if csRaw == "" {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s missing checksum (corrupt), backup to %s\n", path, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, expectedShardID, nil
		}
		expected := computeChecksum(sf.Data)
		if expected != sf.Checksum {
			backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
			_ = copyFile(path, backupPath)
			fmt.Fprintf(os.Stderr, "Warning: shard file %s checksum mismatch (corrupt), expected %s got %s, backup to %s\n", path, expected, sf.Checksum, backupPath)
			_ = atomicWrite(path, map[string]interface{}{}, expectedShardID, 0)
			return map[string]interface{}{}, 0, expectedShardID, nil
		}
		ver := 0
		if sf.Version != nil {
			ver = *sf.Version
		}
		sid := expectedShardID
		if sf.ShardID != nil {
			sid = *sf.ShardID
		}
		return sf.Data, ver, sid, nil
	}
	// Old flat format: whole file is data
	return raw, 0, expectedShardID, nil
}

func readShardFile(path string) (map[string]interface{}, error) {
	d, _, _, err := readShardFileWithMeta(path, 0)
	return d, err
}

func isGlobalKey(key string) bool {
	return strings.HasPrefix(key, "global:")
}

func totalWeight(cfg Config) int {
	tot := 0
	for _, s := range cfg.Shards {
		w := 1
		if s.Weight != nil {
			w = *s.Weight
		}
		tot += w
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
			_, _, _, _ = readShardFileWithMeta(s.Path, s.ID)
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
	if isGlobalKey(key) {
		shardsSorted := make([]Shard, len(p.Config.Shards))
		copy(shardsSorted, p.Config.Shards)
		sort.Slice(shardsSorted, func(i, j int) bool { return shardsSorted[i].ID < shardsSorted[j].ID })
		for _, s := range shardsSorted {
			d, _, _, err := readShardFileWithMeta(s.Path, s.ID)
			if err != nil {
				continue
			}
			if v, ok := d[key]; ok {
				return v, true
			}
		}
		leg, _ := p.readLegacy()
		if v, ok := leg[key]; ok {
			return v, true
		}
		return nil, false
	}
	path, err := p.GetShardPath(key)
	if err != nil {
		return nil, false
	}
	m, _, _, err := readShardFileWithMeta(path, p.GetShardID(key))
	if err != nil {
		return nil, false
	}
	v, ok := m[key]
	if ok {
		return v, true
	}
	leg, _ := p.readLegacy()
	v2, ok2 := leg[key]
	return v2, ok2
}

func (p *ShardingProxy) Set(key string, value interface{}) error {
	if isGlobalKey(key) {
		for _, s := range p.Config.Shards {
			m, ver, _, err := readShardFileWithMeta(s.Path, s.ID)
			if err != nil {
				return err
			}
			m[key] = value
			if err := atomicWrite(s.Path, m, s.ID, ver+1); err != nil {
				return err
			}
		}
		_ = appendOpsLog(p.OpsLogPath, "set", key, value, -1, 0)
		return nil
	}
	sid := p.GetShardID(key)
	path, err := p.GetShardPath(key)
	if err != nil {
		return err
	}
	m, ver, _, err := readShardFileWithMeta(path, sid)
	if err != nil {
		return err
	}
	m[key] = value
	if err := atomicWrite(path, m, sid, ver+1); err != nil {
		return err
	}
	_ = appendOpsLog(p.OpsLogPath, "set", key, value, sid, ver+1)
	return nil
}

func (p *ShardingProxy) Delete(key string) (bool, error) {
	if isGlobalKey(key) {
		deleted := false
		for _, s := range p.Config.Shards {
			m, ver, _, err := readShardFileWithMeta(s.Path, s.ID)
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
			_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, -1, 0)
		}
		return deleted, nil
	}
	sid := p.GetShardID(key)
	path, err := p.GetShardPath(key)
	if err != nil {
		return false, err
	}
	m, ver, _, err := readShardFileWithMeta(path, sid)
	if err != nil {
		return false, err
	}
	if _, ok := m[key]; ok {
		delete(m, key)
		if err := atomicWrite(path, m, sid, ver+1); err != nil {
			return false, err
		}
		_ = appendOpsLog(p.OpsLogPath, "delete", key, nil, sid, ver+1)
		return true, nil
	}
	return false, nil
}

func (p *ShardingProxy) GetAllKeys() ([]string, error) {
	keysMap := make(map[string]struct{})
	for _, s := range p.Config.Shards {
		m, _, _, err := readShardFileWithMeta(s.Path, s.ID)
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
		m, _, _, err := readShardFileWithMeta(s.Path, s.ID)
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

func getUpdatedAt(v interface{}) *int64 {
	if m, ok := v.(map[string]interface{}); ok {
		if ts, ok := m["updated_at"]; ok {
			switch t := ts.(type) {
			case float64:
				i := int64(t)
				return &i
			case int:
				i := int64(t)
				return &i
			case int64:
				return &t
			case json.Number:
				if i, err := t.Int64(); err == nil {
					return &i
				}
			}
		}
	}
	return nil
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
	// Sort ops by ts
	sort.Slice(opsEntries, func(i, j int) bool {
		tsi, _ := opsEntries[i]["ts"].(float64)
		tsj, _ := opsEntries[j]["ts"].(float64)
		// if float missing, try int
		if _, ok := opsEntries[i]["ts"].(int64); ok {
			tsi = float64(opsEntries[i]["ts"].(int64))
		}
		if _, ok := opsEntries[j]["ts"].(int64); ok {
			tsj = float64(opsEntries[j]["ts"].(int64))
		}
		return tsi < tsj
	})

	currentData := make(map[int]map[string]interface{})
	currentVersions := make(map[int]int)
	keyToShards := make(map[string][]int)
	for _, s := range cfg.Shards {
		m, ver, _, err := readShardFileWithMeta(s.Path, s.ID)
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

	// staging dir for atomicity across all shards
	stagingDir := "/app/data/staging"
	_ = os.MkdirAll(stagingDir, 0755)
	// Clean staging
	files, _ := os.ReadDir(stagingDir)
	for _, f := range files {
		_ = os.Remove(filepath.Join(stagingDir, f.Name()))
	}

	// Prepare staged files
	stagedPaths := make(map[int]string)
	finalMerged := make(map[int]map[string]interface{})
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
		// misplaced
		if mg, ok := misplacedGrouped[sid]; ok {
			for k, v := range mg {
				if _, exists := base[k]; !exists {
					base[k] = v
				}
			}
		}
		// legacy with updated_at conflict resolution
		if lg, ok := legacyGrouped[sid]; ok {
			for k, v := range lg {
				if existing, exists := base[k]; exists && !force {
					// timestamp-based conflict resolution
					existingTs := getUpdatedAt(existing)
					newTs := getUpdatedAt(v)
					if existingTs != nil && newTs != nil {
						if *newTs > *existingTs {
							fmt.Fprintf(os.Stderr, "Overwriting key '%s' in shard %d due to newer updated_at (%d > %d)\n", k, sid, *newTs, *existingTs)
							base[k] = v
						} else {
							// keep existing
						}
					} else {
						// preserve existing
					}
				} else {
					if _, exists := base[k]; exists && force {
						if fmt.Sprintf("%v", base[k]) != fmt.Sprintf("%v", v) {
							fmt.Fprintf(os.Stderr, "Overwriting key '%s' in shard %d\n", k, sid)
						}
					}
					base[k] = v
				}
			}
		}
		finalMerged[sid] = base
		stagedPath := filepath.Join(stagingDir, fmt.Sprintf("shard_%d.json.tmp", sid))
		ver := currentVersions[sid] + 1
		if err := func() error {
			// write staged file with new format including shard_id and version
			checksum := computeChecksum(base)
			sf := ShardFile{ShardID: func() *int { i := sid; return &i }(), Version: func() *int { i := ver; return &i }(), Data: base, Checksum: checksum}
			dir := filepath.Dir(stagedPath)
			_ = os.MkdirAll(dir, 0755)
			tmpF, err := os.CreateTemp(dir, "tmp-*.json")
			if err != nil {
				return err
			}
			tmpP := tmpF.Name()
			enc := json.NewEncoder(tmpF)
			enc.SetIndent("", "  ")
			enc.SetEscapeHTML(false)
			if err := enc.Encode(sf); err != nil {
				tmpF.Close()
				os.Remove(tmpP)
				return err
			}
			tmpF.Close()
			return os.Rename(tmpP, stagedPath)
		}(); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to stage shard %d: %v\n", sid, err)
			// rollback
			if backupPath != "" {
				for _, s := range cfg.Shards {
					bak := s.Path + ".bak"
					if _, err := os.Stat(bak); err == nil {
						_ = copyFileIO(bak, s.Path)
					}
				}
			}
			return err
		}
		stagedPaths[sid] = stagedPath
		_ = shardPath
	}

	// Commit staged to final atomically
	for sid, stagedPath := range stagedPaths {
		finalPath := idToPath[sid]
		if err := os.Rename(stagedPath, finalPath); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to commit staged shard %d: %v\n", sid, err)
			if backupPath != "" {
				for _, s := range cfg.Shards {
					bak := s.Path + ".bak"
					if _, err := os.Stat(bak); err == nil {
						_ = copyFileIO(bak, s.Path)
					}
				}
			}
			return err
		}
	}

	// Clean staging
	_ = os.RemoveAll(stagingDir)
	_ = os.MkdirAll(stagingDir, 0755)

	// Replay ops.log after legacy migration (tombstone handling)
	// Need to reload merged data after commit, then apply ops log
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
					m, ver, _, _ := readShardFileWithMeta(s.Path, s.ID)
					m[key] = val
					_ = atomicWrite(s.Path, m, s.ID, ver+1)
				}
			} else {
				sid := hashKeyWeighted(key, cfg)
				if sid == -1 {
					continue
				}
				path := idToPath[sid]
				m, ver, _, _ := readShardFileWithMeta(path, sid)
				m[key] = val
				_ = atomicWrite(path, m, sid, ver+1)
			}
		} else if op == "delete" {
			if isGlobalKey(key) {
				for _, s := range cfg.Shards {
					m, ver, _, _ := readShardFileWithMeta(s.Path, s.ID)
					if _, ok := m[key]; ok {
						delete(m, key)
						_ = atomicWrite(s.Path, m, s.ID, ver+1)
					}
				}
			} else {
				sid := hashKeyWeighted(key, cfg)
				path := idToPath[sid]
				m, ver, _, _ := readShardFileWithMeta(path, sid)
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

	// For global consistency: ensure all shards have same value for global keys
	globalKeys := make(map[string]interface{})
	for _, s := range cfg.Shards {
		m, _, _, _ := readShardFileWithMeta(s.Path, s.ID)
		for k, v := range m {
			if isGlobalKey(k) {
				if _, ok := globalKeys[k]; !ok {
					globalKeys[k] = v
				} else {
					// if inconsistent, resolve to latest updated_at or keep existing
					// For simplicity, keep first, but if force, we already overwrote? We'll ensure consistency by replicating first found to all
				}
			}
		}
	}
	for k, v := range globalKeys {
		for _, s := range cfg.Shards {
			m, ver, _, _ := readShardFileWithMeta(s.Path, s.ID)
			if existing, ok := m[k]; ok {
				// if inconsistent, keep latest updated_at
				if fmt.Sprintf("%v", existing) != fmt.Sprintf("%v", v) {
					// resolve by updated_at
					exTs := getUpdatedAt(existing)
					newTs := getUpdatedAt(v)
					if exTs != nil && newTs != nil && *newTs > *exTs {
						m[k] = v
						_ = atomicWrite(s.Path, m, s.ID, ver+1)
					} else if exTs == nil && newTs == nil {
						// keep existing for now, but ensure all shards have same value (replicate v to all)
						// Actually to ensure consistency, replicate globalKeys value to all shards
						m[k] = v
						_ = atomicWrite(s.Path, m, s.ID, ver+1)
					}
				}
			} else {
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
  migrate [--dry-run] [--backup path] [--force]  migrates legacy, cleans duplicates, replays ops.log, uses staging
Flags:
  --config string (default /app/config.json)
  --legacy string (default /app/data/legacy.json)
  --ops-log string (default /app/data/ops.log)
  --help -h help
  weight per shard via config weight field, default 1, must be >0 if present
  global: prefix for broadcast
  version, shard_id, checksum integrity
  dry-run, backup, force, ops.log replay, updated_at conflict resolution
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
					// known, no value
				} else if strings.HasPrefix(a, "--backup=") {
					// known with =value, no extra skip
				} else if a == "--backup" {
					// known, skip value
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

echo "Turn2 hard solution applied"
go build -o ./proxy_bin . && echo "Build OK" && rm -f ./proxy_bin
