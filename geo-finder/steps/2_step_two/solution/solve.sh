#!/bin/bash
set -e
mkdir -p /app/src
cat > /app/src/go.mod <<'GOMOD'
module geofence

go 1.22
GOMOD

cat > /app/src/main.go <<'GOEOF'
package main

import (
	"container/list"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Point struct {
	Lat float64 `json:"lat"`
	Lng float64 `json:"lng"`
}

type Geofence struct {
	ID      string  `json:"id"`
	Name    string  `json:"name"`
	Polygon []Point `json:"polygon"`
}

type DB map[string]Geofence

type BBox struct {
	MinLat, MaxLat float64
	MinLng, MaxLng float64
}

type CellKey struct {
	LatIdx int
	LngIdx int
}

func (c CellKey) String() string {
	return fmt.Sprintf("%d_%d", c.LatIdx, c.LngIdx)
}

var idRegex = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func printErrExit(code int, format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
	os.Exit(code)
}

func parseDBFlag() (string, []string) {
	dbPath := ""
	args := os.Args[1:]
	filtered := []string{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--db" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --db")
			}
			dbPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--db=") {
			dbPath = strings.TrimPrefix(a, "--db=")
		} else {
			filtered = append(filtered, a)
		}
	}
	return dbPath, filtered
}

func loadDB(path string) DB {
	if path == "" {
		printErrExit(2, "--db flag is required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return make(DB)
		}
		printErrExit(4, "failed to read db: %v", err)
	}
	if len(data) == 0 {
		return make(DB)
	}
	var db DB
	if err := json.Unmarshal(data, &db); err != nil {
		printErrExit(4, "corrupt DB: %v", err)
	}
	if db == nil {
		db = make(DB)
	}
	return db
}

func atomicWriteDB(path string, db DB) {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		printErrExit(2, "failed to create db dir: %v", err)
	}
	tmpPath := fmt.Sprintf("%s.tmp.%d", path, os.Getpid())
	f, err := os.Create(tmpPath)
	if err != nil {
		printErrExit(2, "failed to create tmp db: %v", err)
	}
	enc := json.NewEncoder(f)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(db); err != nil {
		f.Close()
		os.Remove(tmpPath)
		printErrExit(2, "failed to write db: %v", err)
	}
	f.Sync()
	f.Close()
	if err := os.Rename(tmpPath, path); err != nil {
		os.Remove(tmpPath)
		printErrExit(2, "failed to rename db: %v", err)
	}
}

func validateLat(lat float64) bool { return lat >= -90 && lat <= 90 }
func validateLng(lng float64) bool { return lng >= -180 && lng <= 180 }

func parsePolygonString(s string) ([]Point, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil, fmt.Errorf("empty polygon")
	}
	parts := strings.Split(s, ";")
	points := make([]Point, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		coords := strings.Split(p, ",")
		if len(coords) != 2 {
			return nil, fmt.Errorf("invalid point format %q", p)
		}
		latStr := strings.TrimSpace(coords[0])
		lngStr := strings.TrimSpace(coords[1])
		lat, err1 := strconv.ParseFloat(latStr, 64)
		lng, err2 := strconv.ParseFloat(lngStr, 64)
		if err1 != nil || err2 != nil {
			return nil, fmt.Errorf("invalid float in %q", p)
		}
		if !validateLat(lat) || !validateLng(lng) {
			return nil, fmt.Errorf("lat/lng out of range in %q", p)
		}
		points = append(points, Point{Lat: lat, Lng: lng})
	}
	if len(points) < 3 {
		return nil, fmt.Errorf("polygon must have at least 3 points")
	}
	if len(points) > 1000 {
		return nil, fmt.Errorf("polygon too large >1000 points")
	}
	return points, nil
}

const eps = 1e-9

func pointOnSegment(px, py, x1, y1, x2, y2 float64) bool {
	cross := (x2-x1)*(py-y1) - (y2-y1)*(px-x1)
	if math.Abs(cross) > eps {
		return false
	}
	minX := math.Min(x1, x2) - eps
	maxX := math.Max(x1, x2) + eps
	minY := math.Min(y1, y2) - eps
	maxY := math.Max(y1, y2) + eps
	if px < minX || px > maxX || py < minY || py > maxY {
		return false
	}
	return true
}

func pointInPolygon(lat, lng float64, poly []Point) bool {
	px := lng
	py := lat
	n := len(poly)
	for i := 0; i < n; i++ {
		j := (i + 1) % n
		x1 := poly[i].Lng
		y1 := poly[i].Lat
		x2 := poly[j].Lng
		y2 := poly[j].Lat
		if pointOnSegment(px, py, x1, y1, x2, y2) {
			return true
		}
	}
	inside := false
	j := n - 1
	for i := 0; i < n; i++ {
		xi := poly[i].Lng
		yi := poly[i].Lat
		xj := poly[j].Lng
		yj := poly[j].Lat
		if (yi > py) != (yj > py) {
			xIntersect := (xj-xi)*(py-yi)/(yj-yi) + xi
			if px < xIntersect {
				inside = !inside
			}
		}
		j = i
	}
	return inside
}

func computeBBox(poly []Point) BBox {
	if len(poly) == 0 {
		return BBox{}
	}
	minLat := poly[0].Lat
	maxLat := poly[0].Lat
	minLng := poly[0].Lng
	maxLng := poly[0].Lng
	for _, p := range poly[1:] {
		if p.Lat < minLat {
			minLat = p.Lat
		}
		if p.Lat > maxLat {
			maxLat = p.Lat
		}
		if p.Lng < minLng {
			minLng = p.Lng
		}
		if p.Lng > maxLng {
			maxLng = p.Lng
		}
	}
	return BBox{MinLat: minLat, MaxLat: maxLat, MinLng: minLng, MaxLng: maxLng}
}

type cacheEntry struct {
	key   string
	value []string
}

type LRUCache struct {
	capacity int
	mu       sync.Mutex
	ll       *list.List
	cache    map[string]*list.Element
}

func NewLRUCache(capacity int) *LRUCache {
	return &LRUCache{
		capacity: capacity,
		ll:       list.New(),
		cache:    make(map[string]*list.Element),
	}
}

func (c *LRUCache) Get(key string) ([]string, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if elem, ok := c.cache[key]; ok {
		c.ll.MoveToFront(elem)
		val := elem.Value.(*cacheEntry).value
		cp := make([]string, len(val))
		copy(cp, val)
		return cp, true
	}
	return nil, false
}

func (c *LRUCache) Put(key string, value []string) {
	if c.capacity <= 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if elem, ok := c.cache[key]; ok {
		c.ll.MoveToFront(elem)
		elem.Value.(*cacheEntry).value = value
		return
	}
	ent := &cacheEntry{key: key, value: value}
	elem := c.ll.PushFront(ent)
	c.cache[key] = elem
	if c.ll.Len() > c.capacity {
		back := c.ll.Back()
		if back != nil {
			c.ll.Remove(back)
			be := back.Value.(*cacheEntry)
			delete(c.cache, be.key)
		}
	}
}

func (c *LRUCache) Size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.ll.Len()
}

type Server struct {
	db             DB
	bboxes         map[string]BBox
	grid           map[CellKey][]string
	gridSize       float64
	cache          *LRUCache
	mu             sync.RWMutex
	totalQueries   int64
	cacheHits      int64
	totalLatencyNs int64
}

func NewServer(db DB, gridSize float64, cacheSize int) *Server {
	s := &Server{
		db:       db,
		bboxes:   make(map[string]BBox),
		grid:     make(map[CellKey][]string),
		gridSize: gridSize,
		cache:    NewLRUCache(cacheSize),
	}
	for id, g := range db {
		bbox := computeBBox(g.Polygon)
		s.bboxes[id] = bbox
		latStart := int(math.Floor((bbox.MinLat + 90.0) / gridSize))
		latEnd := int(math.Floor((bbox.MaxLat + 90.0) / gridSize))
		lngStart := int(math.Floor((bbox.MinLng + 180.0) / gridSize))
		lngEnd := int(math.Floor((bbox.MaxLng + 180.0) / gridSize))
		for latIdx := latStart; latIdx <= latEnd; latIdx++ {
			for lngIdx := lngStart; lngIdx <= lngEnd; lngIdx++ {
				key := CellKey{LatIdx: latIdx, LngIdx: lngIdx}
				s.grid[key] = append(s.grid[key], id)
			}
		}
	}
	return s
}

func (s *Server) getCellForPoint(lat, lng float64) CellKey {
	latIdx := int(math.Floor((lat + 90.0) / s.gridSize))
	lngIdx := int(math.Floor((lng + 180.0) / s.gridSize))
	return CellKey{LatIdx: latIdx, LngIdx: lngIdx}
}

func (s *Server) lookupPointInternal(lat, lng float64) []string {
	cell := s.getCellForPoint(lat, lng)
	s.mu.RLock()
	candidates := s.grid[cell]
	candCopy := make([]string, len(candidates))
	copy(candCopy, candidates)
	s.mu.RUnlock()

	matching := []string{}
	for _, id := range candCopy {
		s.mu.RLock()
		g, ok := s.db[id]
		bbox, bboxOk := s.bboxes[id]
		s.mu.RUnlock()
		if !ok {
			continue
		}
		if bboxOk {
			if lat < bbox.MinLat-eps || lat > bbox.MaxLat+eps || lng < bbox.MinLng-eps || lng > bbox.MaxLng+eps {
				continue
			}
		}
		if pointInPolygon(lat, lng, g.Polygon) {
			matching = append(matching, id)
		}
	}
	sort.Strings(matching)
	return matching
}

func (s *Server) lookupWithCache(lat, lng float64) (result []string, isHit bool, durationNs int64) {
	key := fmt.Sprintf("%.6f,%.6f", lat, lng)
	start := time.Now()
	if s.cache.capacity > 0 {
		if val, ok := s.cache.Get(key); ok {
			durationNs = time.Since(start).Nanoseconds()
			return val, true, durationNs
		}
	}
	res := s.lookupPointInternal(lat, lng)
	durationNs = time.Since(start).Nanoseconds()
	if s.cache.capacity > 0 {
		s.cache.Put(key, res)
	}
	return res, false, durationNs
}

func (s *Server) lookupPoint(lat, lng float64) []string {
	res, _, _ := s.lookupWithCache(lat, lng)
	return res
}

func (s *Server) handleLookup(w http.ResponseWriter, r *http.Request) {
	latStr := r.URL.Query().Get("lat")
	lngStr := r.URL.Query().Get("lng")
	if latStr == "" || lngStr == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "lat and lng required"})
		return
	}
	lat, err1 := strconv.ParseFloat(latStr, 64)
	lng, err2 := strconv.ParseFloat(lngStr, 64)
	if err1 != nil || err2 != nil || !validateLat(lat) || !validateLng(lng) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid lat/lng"})
		return
	}
	result, isHit, durNs := s.lookupWithCache(lat, lng)
	atomic.AddInt64(&s.totalQueries, 1)
	if isHit {
		atomic.AddInt64(&s.cacheHits, 1)
	}
	atomic.AddInt64(&s.totalLatencyNs, durNs)

	resp := map[string]interface{}{
		"lat":       lat,
		"lng":       lng,
		"geofences": result,
		"count":     len(result),
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func (s *Server) handleBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(405)
		json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
		return
	}
	var req struct {
		Points []Point `json:"points"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}
	if len(req.Points) > 1000 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "batch too large"})
		return
	}
	for _, p := range req.Points {
		if !validateLat(p.Lat) || !validateLng(p.Lng) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid lat/lng in batch"})
			return
		}
	}
	results := make([]map[string]interface{}, len(req.Points))
	var wg sync.WaitGroup
	type batchRes struct {
		idx   int
		lat   float64
		lng   float64
		geofs []string
		isHit bool
		durNs int64
	}
	ch := make(chan batchRes, len(req.Points))
	for i, pt := range req.Points {
		wg.Add(1)
		go func(idx int, lat, lng float64) {
			defer wg.Done()
			geofs, isHit, dur := s.lookupWithCache(lat, lng)
			ch <- batchRes{idx: idx, lat: lat, lng: lng, geofs: geofs, isHit: isHit, durNs: dur}
		}(i, pt.Lat, pt.Lng)
	}
	wg.Wait()
	close(ch)
	var totalDur int64
	var hitCount int64
	var qCount int64
	for br := range ch {
		results[br.idx] = map[string]interface{}{
			"lat":       br.lat,
			"lng":       br.lng,
			"geofences": br.geofs,
		}
		totalDur += br.durNs
		if br.isHit {
			hitCount++
		}
		qCount++
	}
	atomic.AddInt64(&s.totalQueries, qCount)
	atomic.AddInt64(&s.cacheHits, hitCount)
	atomic.AddInt64(&s.totalLatencyNs, totalDur)

	resp := map[string]interface{}{
		"results": results,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func (s *Server) handleGeofences(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	ids := make([]string, 0, len(s.db))
	for id := range s.db {
		ids = append(ids, id)
	}
	s.mu.RUnlock()
	sort.Strings(ids)
	s.mu.RLock()
	list := make([]Geofence, 0, len(ids))
	for _, id := range ids {
		list = append(list, s.db[id])
	}
	s.mu.RUnlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(list)
}

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	totalQ := atomic.LoadInt64(&s.totalQueries)
	hits := atomic.LoadInt64(&s.cacheHits)
	totalNs := atomic.LoadInt64(&s.totalLatencyNs)
	avgMs := 0.0
	if totalQ > 0 {
		avgMs = float64(totalNs) / float64(totalQ) / 1e6
	}
	s.mu.RLock()
	totalGeof := len(s.db)
	indexCells := len(s.grid)
	s.mu.RUnlock()
	cacheSize := s.cache.Size()

	resp := map[string]interface{}{
		"total_geofences": totalGeof,
		"total_queries":   totalQ,
		"cache_hits":      hits,
		"cache_size":      cacheSize,
		"avg_latency_ms":  avgMs,
		"index_cells":     indexCells,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func cmdAdd(dbPath string, args []string) {
	if len(args) < 1 {
		printErrExit(2, "add requires <id>")
	}
	id := args[0]
	if !idRegex.MatchString(id) {
		printErrExit(2, "invalid id format")
	}
	polygonStr := ""
	name := ""
	rest := args[1:]
	for i := 0; i < len(rest); i++ {
		a := rest[i]
		if a == "--polygon" {
			if i+1 >= len(rest) {
				printErrExit(2, "missing value for --polygon")
			}
			polygonStr = rest[i+1]
			i++
		} else if strings.HasPrefix(a, "--polygon=") {
			polygonStr = strings.TrimPrefix(a, "--polygon=")
		} else if a == "--name" {
			if i+1 >= len(rest) {
				printErrExit(2, "missing value for --name")
			}
			name = rest[i+1]
			i++
		} else if strings.HasPrefix(a, "--name=") {
			name = strings.TrimPrefix(a, "--name=")
		} else {
			printErrExit(2, "unknown flag %s for add", a)
		}
	}
	if polygonStr == "" {
		printErrExit(2, "--polygon is required")
	}
	if name == "" {
		printErrExit(2, "--name is required")
	}
	trimmedName := strings.TrimSpace(name)
	if trimmedName == "" || len(trimmedName) > 128 {
		printErrExit(2, "invalid name length")
	}
	if strings.Contains(name, "\n") {
		printErrExit(2, "name contains newline")
	}
	points, err := parsePolygonString(polygonStr)
	if err != nil {
		printErrExit(2, "invalid polygon: %v", err)
	}
	db := loadDB(dbPath)
	g := Geofence{
		ID:      id,
		Name:    trimmedName,
		Polygon: points,
	}
	db[id] = g
	atomicWriteDB(dbPath, db)
	out, _ := json.Marshal(g)
	fmt.Println(string(out))
}

func cmdRemove(dbPath string, args []string) {
	if len(args) < 1 {
		printErrExit(2, "remove requires <id>")
	}
	id := args[0]
	if !idRegex.MatchString(id) {
		printErrExit(2, "invalid id format")
	}
	if len(args) > 1 {
		printErrExit(2, "remove takes only <id>")
	}
	db := loadDB(dbPath)
	if _, ok := db[id]; !ok {
		printErrExit(3, "geofence %s not found", id)
	}
	delete(db, id)
	atomicWriteDB(dbPath, db)
	fmt.Println("removed")
}

func cmdList(dbPath string, args []string) {
	if len(args) != 0 {
		printErrExit(2, "list takes no args")
	}
	db := loadDB(dbPath)
	ids := make([]string, 0, len(db))
	for k := range db {
		ids = append(ids, k)
	}
	sort.Strings(ids)
	list := make([]Geofence, 0, len(ids))
	for _, id := range ids {
		list = append(list, db[id])
	}
	out, _ := json.Marshal(list)
	fmt.Println(string(out))
}

func cmdLookup(dbPath string, args []string) {
	latStr := ""
	lngStr := ""
	verbose := false
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--lat" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --lat")
			}
			latStr = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--lat=") {
			latStr = strings.TrimPrefix(a, "--lat=")
		} else if a == "--lng" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --lng")
			}
			lngStr = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--lng=") {
			lngStr = strings.TrimPrefix(a, "--lng=")
		} else if a == "--verbose" {
			verbose = true
		} else {
			printErrExit(2, "unknown flag %s for lookup", a)
		}
	}
	if latStr == "" || lngStr == "" {
		printErrExit(2, "--lat and --lng are required")
	}
	lat, err1 := strconv.ParseFloat(latStr, 64)
	lng, err2 := strconv.ParseFloat(lngStr, 64)
	if err1 != nil || err2 != nil {
		printErrExit(2, "invalid lat/lng float")
	}
	if !validateLat(lat) || !validateLng(lng) {
		printErrExit(2, "lat/lng out of range")
	}
	db := loadDB(dbPath)
	matchingIDs := []string{}
	matchingGeofences := []Geofence{}
	for _, g := range db {
		if pointInPolygon(lat, lng, g.Polygon) {
			matchingIDs = append(matchingIDs, g.ID)
			matchingGeofences = append(matchingGeofences, g)
		}
	}
	sort.Strings(matchingIDs)
	sort.Slice(matchingGeofences, func(i, j int) bool {
		return matchingGeofences[i].ID < matchingGeofences[j].ID
	})
	if verbose {
		out, _ := json.Marshal(matchingGeofences)
		fmt.Println(string(out))
	} else {
		out, _ := json.Marshal(matchingIDs)
		fmt.Println(string(out))
	}
}

func cmdClear(dbPath string, args []string) {
	if len(args) != 0 {
		printErrExit(2, "clear takes no args")
	}
	_ = loadDB(dbPath)
	db := make(DB)
	atomicWriteDB(dbPath, db)
	fmt.Println("cleared")
}

func cmdServe(dbPath string, args []string) {
	portStr := ""
	gridSizeStr := ""
	cacheSizeStr := ""

	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--port" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --port")
			}
			portStr = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--port=") {
			portStr = strings.TrimPrefix(a, "--port=")
		} else if a == "--grid-size" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --grid-size")
			}
			gridSizeStr = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--grid-size=") {
			gridSizeStr = strings.TrimPrefix(a, "--grid-size=")
		} else if a == "--cache-size" {
			if i+1 >= len(args) {
				printErrExit(2, "missing value for --cache-size")
			}
			cacheSizeStr = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--cache-size=") {
			cacheSizeStr = strings.TrimPrefix(a, "--cache-size=")
		} else {
			printErrExit(2, "unknown flag %s for serve", a)
		}
	}
	if portStr == "" {
		printErrExit(2, "--port is required")
	}
	port, err := strconv.Atoi(portStr)
	if err != nil || port < 1 || port > 65535 {
		printErrExit(2, "invalid --port")
	}
	gridSize := 1.0
	if gridSizeStr != "" {
		gs, err := strconv.ParseFloat(gridSizeStr, 64)
		if err != nil || gs <= 0 || gs > 45 {
			printErrExit(2, "invalid --grid-size, must be >0 and <=45")
		}
		gridSize = gs
	}
	cacheSize := 1000
	if cacheSizeStr != "" {
		cs, err := strconv.Atoi(cacheSizeStr)
		if err != nil || cs < 0 {
			printErrExit(2, "invalid --cache-size, must be >=0")
		}
		cacheSize = cs
	}
	db := loadDB(dbPath)
	server := NewServer(db, gridSize, cacheSize)

	mux := http.NewServeMux()
	mux.HandleFunc("/lookup", server.handleLookup)
	mux.HandleFunc("/lookup/batch", server.handleBatch)
	mux.HandleFunc("/geofences", server.handleGeofences)
	mux.HandleFunc("/stats", server.handleStats)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(404)
		json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
	})

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Printf("serving on :%d\n", port)
	if err := http.ListenAndServe(addr, mux); err != nil {
		printErrExit(2, "server error: %v", err)
	}
}

func main() {
	dbPath, filtered := parseDBFlag()
	if dbPath == "" {
		printErrExit(2, "--db flag is required")
	}
	if len(filtered) == 0 {
		printErrExit(2, "command required: add, remove, list, lookup, clear, serve")
	}
	cmd := filtered[0]
	args := filtered[1:]
	switch cmd {
	case "add":
		cmdAdd(dbPath, args)
	case "remove":
		cmdRemove(dbPath, args)
	case "list":
		cmdList(dbPath, args)
	case "lookup":
		cmdLookup(dbPath, args)
	case "clear":
		cmdClear(dbPath, args)
	case "serve":
		cmdServe(dbPath, args)
	default:
		printErrExit(2, "unknown command %s", cmd)
	}
}
GOEOF

cd /app/src
go mod tidy
go build -o geofencectl .
echo "solved"
