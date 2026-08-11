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
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
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
	var raw json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		printErrExit(4, "corrupt DB: %v", err)
	}
	// must be object
	var db DB
	if err := json.Unmarshal(data, &db); err != nil {
		printErrExit(4, "corrupt DB: %v", err)
	}
	// check if it's array then corrupt per spec
	// We already try to unmarshal as object; if data is [] it would succeed as empty? Actually DB map from [] would fail? In Go, json.Unmarshal array into map fails.
	// So we check if data trimmed starts with '['
	trim := strings.TrimSpace(string(data))
	if strings.HasPrefix(trim, "[") {
		printErrExit(4, "corrupt DB: expected object, got array")
	}
	if db == nil {
		db = make(DB)
	}
	return db
}

func atomicWriteDB(path string, db DB) {
	if err := atomicWriteDBErr(path, db); err != nil {
		printErrExit(2, "failed to write db: %v", err)
	}
}

func atomicWriteDBErr(path string, db DB) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmpPath := fmt.Sprintf("%s.tmp.%d", path, os.Getpid())
	f, err := os.Create(tmpPath)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(db); err != nil {
		f.Close()
		os.Remove(tmpPath)
		return err
	}
	f.Sync()
	f.Close()
	if err := os.Rename(tmpPath, path); err != nil {
		os.Remove(tmpPath)
		return err
	}
	return nil
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
	for idx, p := range parts {
		pTrim := strings.TrimSpace(p)
		if pTrim == "" {
			return nil, fmt.Errorf("empty segment at position %d", idx)
		}
		coords := strings.Split(pTrim, ",")
		if len(coords) != 2 {
			return nil, fmt.Errorf("invalid point format %q", pTrim)
		}
		latStr := strings.TrimSpace(coords[0])
		lngStr := strings.TrimSpace(coords[1])
		if latStr == "" || lngStr == "" {
			return nil, fmt.Errorf("empty lat/lng in %q", pTrim)
		}
		lat, err1 := strconv.ParseFloat(latStr, 64)
		lng, err2 := strconv.ParseFloat(lngStr, 64)
		if err1 != nil || err2 != nil {
			return nil, fmt.Errorf("invalid float in %q", pTrim)
		}
		if !validateLat(lat) || !validateLng(lng) {
			return nil, fmt.Errorf("lat/lng out of range in %q", pTrim)
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

// ---- strict validation ----

const eps = 1e-9

func pointsEqual(a, b Point) bool {
	return a.Lat == b.Lat && a.Lng == b.Lng
	// Could use eps but spec says exact duplicate check
}

func hasDuplicatePoints(poly []Point) bool {
	n := len(poly)
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			if pointsEqual(poly[i], poly[j]) {
				return true
			}
		}
	}
	return false
}

func polygonArea(poly []Point) float64 {
	// shoelace, x=lng y=lat
	n := len(poly)
	if n < 3 {
		return 0
	}
	area := 0.0
	for i := 0; i < n; i++ {
		j := (i + 1) % n
		area += poly[i].Lng*poly[j].Lat - poly[j].Lng*poly[i].Lat
	}
	return area / 2.0
}

func orientation(p, q, r Point) int {
	// cross (q-p) x (r-p) where x=lng, y=lat
	// val = (q.x-p.x)*(r.y-p.y) - (q.y-p.y)*(r.x-p.x)
	val := (q.Lng-p.Lng)*(r.Lat-p.Lat) - (q.Lat-p.Lat)*(r.Lng-p.Lng)
	if math.Abs(val) < eps {
		return 0
	}
	if val > 0 {
		return 1 // clockwise? actually depends but >0 is ccw in math
	}
	return 2
}

func onSegment(p, q, r Point) bool {
	// q lies on segment pr
	if q.Lng <= math.Max(p.Lng, r.Lng)+eps && q.Lng >= math.Min(p.Lng, r.Lng)-eps &&
		q.Lat <= math.Max(p.Lat, r.Lat)+eps && q.Lat >= math.Min(p.Lat, r.Lat)-eps {
		return true
	}
	return false
}

func segmentsIntersect(p1, q1, p2, q2 Point) bool {
	o1 := orientation(p1, q1, p2)
	o2 := orientation(p1, q1, q2)
	o3 := orientation(p2, q2, p1)
	o4 := orientation(p2, q2, q1)

	if o1 != o2 && o3 != o4 {
		return true
	}
	// special colinear
	if o1 == 0 && onSegment(p1, p2, q1) {
		return true
	}
	if o2 == 0 && onSegment(p1, q2, q1) {
		return true
	}
	if o3 == 0 && onSegment(p2, p1, q2) {
		return true
	}
	if o4 == 0 && onSegment(p2, q1, q2) {
		return true
	}
	return false
}

func isSelfIntersecting(poly []Point) bool {
	n := len(poly)
	if n < 4 {
		return false
	}
	// check all pairs of non-adjacent edges
	for i := 0; i < n; i++ {
		i2 := (i + 1) % n
		p1 := poly[i]
		q1 := poly[i2]
		// zero-length edge is duplicate, but duplicate already caught; if zero length, treat as self-intersect
		if pointsEqual(p1, q1) {
			return true
		}
		for j := i + 1; j < n; j++ {
			j2 := (j + 1) % n
			// skip same edge
			if i == j {
				continue
			}
			// skip adjacent edges sharing a vertex
			if i2 == j || j2 == i || i == j2 || i2 == j2 {
				continue
			}
			// also skip if edges are consecutive: j == i+1 or i == j+1 mod n
			// already covered by adjacency above but ensure
			// check sharing vertex already
			p2 := poly[j]
			q2 := poly[j2]
			// if any endpoints equal (duplicate point not adjacent) -> self-intersection via duplicate, but duplicate already caught
			// still check intersection
			if segmentsIntersect(p1, q1, p2, q2) {
				return true
			}
		}
	}
	return false
}

func validatePolygon(poly []Point) error {
	if len(poly) < 3 {
		return fmt.Errorf("at least 3 points required")
	}
	if len(poly) > 1000 {
		return fmt.Errorf("too many points >1000")
	}
	for _, p := range poly {
		if !validateLat(p.Lat) || !validateLng(p.Lng) {
			return fmt.Errorf("lat/lng out of range")
		}
	}
	if hasDuplicatePoints(poly) {
		return fmt.Errorf("duplicate points")
	}
	area := polygonArea(poly)
	if math.Abs(area) < eps {
		return fmt.Errorf("degenerate area zero")
	}
	if isSelfIntersecting(poly) {
		return fmt.Errorf("self-intersecting")
	}
	return nil
}

func pointOnSegment(px, py, x1, y1, x2, y2 float64) bool {
	cross := (x2-x1)*(py-y1) - (y2-y1)*(px-x1)
	if math.Abs(cross) > eps {
		return false
	}
	minX := x1
	maxX := x2
	if minX > maxX {
		minX, maxX = maxX, minX
	}
	minY := y1
	maxY := y2
	if minY > maxY {
		minY, maxY = maxY, minY
	}
	if px < minX-eps || px > maxX+eps || py < minY-eps || py > maxY+eps {
		return false
	}
	return true
}

func crossesAntimeridianPoly(poly []Point) bool {
	if len(poly) == 0 {
		return false
	}
	minLng := poly[0].Lng
	maxLng := poly[0].Lng
	for _, p := range poly[1:] {
		if p.Lng < minLng {
			minLng = p.Lng
		}
		if p.Lng > maxLng {
			maxLng = p.Lng
		}
	}
	diff := maxLng - minLng
	if diff < 0 {
		diff = -diff
	}
	// World-spanning (360) should not be treated as small crossing
	if diff >= 360-eps {
		return false
	}
	return diff > 180
}

func pointInPolygon(lat, lng float64, poly []Point) bool {
	// Handle antimeridian crossing by shifting negative lngs +360
	usePoly := poly
	useLng := lng
	if crossesAntimeridianPoly(poly) {
		shifted := make([]Point, len(poly))
		for i, p := range poly {
			if p.Lng < 0 {
				shifted[i] = Point{Lat: p.Lat, Lng: p.Lng + 360}
			} else {
				shifted[i] = p
			}
		}
		usePoly = shifted
		if lng < 0 {
			useLng = lng + 360
		}
		// If after shifting, the polygon still has large span, we may need to handle case where query lng is on other side of 0
		// For small crossing rectangles, shifted lng range is e.g., 179-181, so query at 0 stays 0 (outside) correctly.
		// For query at e.g., -179.5 shifted to 180.5 inside.
	}
	px := useLng
	py := lat
	n := len(usePoly)
	for i := 0; i < n; i++ {
		j := (i + 1) % n
		x1 := usePoly[i].Lng
		y1 := usePoly[i].Lat
		x2 := usePoly[j].Lng
		y2 := usePoly[j].Lat
		if pointOnSegment(px, py, x1, y1, x2, y2) {
			return true
		}
	}
	// Also check original polygon edges for points exactly on antimeridian edge that might be missed due to shift?
	// For crossing case, the edge that crosses from 179 to -179 via 180 after shift becomes 179->181, so point at 180 is on edge and will be caught.
	// No need for extra check.

	inside := false
	j := n - 1
	for i := 0; i < n; i++ {
		xi := usePoly[i].Lng
		yi := usePoly[i].Lat
		xj := usePoly[j].Lng
		yj := usePoly[j].Lat
		if (yi > py) != (yj > py) {
			// Avoid div by zero (handled by yi!=yj due to condition)
			xIntersect := (xj-xi)*(py-yi)/(yj-yi) + xi
			if px < xIntersect {
				inside = !inside
			}
		}
		j = i
	}
	return inside
}

func pointInBBox(lat, lng float64, bbox BBox) bool {
	if lat < bbox.MinLat-eps || lat > bbox.MaxLat+eps {
		return false
	}
	span := bbox.MaxLng - bbox.MinLng
	if span >= 360-eps {
		// world-spanning, all lngs valid
		return true
	}
	if span > 180 {
		// crossing antimeridian: valid lngs are >= max OR <= min (small wrapping interval)
		// Reject if lng is strictly between min and max (large interior gap)
		if lng > bbox.MinLng+eps && lng < bbox.MaxLng-eps {
			return false
		}
		return true
	}
	if lng < bbox.MinLng-eps || lng > bbox.MaxLng+eps {
		return false
	}
	return true
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

func gridCellsForBBox(bbox BBox, gridSize float64) []CellKey {
	latStart := int(math.Floor((bbox.MinLat + 90.0) / gridSize))
	latEnd := int(math.Floor((bbox.MaxLat + 90.0) / gridSize))
	lngSpan := bbox.MaxLng - bbox.MinLng
	keys := []CellKey{}
	if lngSpan >= 360-eps {
		lngStart := int(math.Floor((-180.0 + 180.0) / gridSize))
		lngEnd := int(math.Floor((180.0 + 180.0) / gridSize))
		for latIdx := latStart; latIdx <= latEnd; latIdx++ {
			for lngIdx := lngStart; lngIdx <= lngEnd; lngIdx++ {
				keys = append(keys, CellKey{LatIdx: latIdx, LngIdx: lngIdx})
			}
		}
	} else if lngSpan > 180 {
		lngStart1 := int(math.Floor((bbox.MaxLng + 180.0) / gridSize))
		lngEnd1 := int(math.Floor((180.0 + 180.0) / gridSize))
		lngStart2 := int(math.Floor((-180.0 + 180.0) / gridSize))
		lngEnd2 := int(math.Floor((bbox.MinLng + 180.0) / gridSize))
		for latIdx := latStart; latIdx <= latEnd; latIdx++ {
			for lngIdx := lngStart1; lngIdx <= lngEnd1; lngIdx++ {
				keys = append(keys, CellKey{LatIdx: latIdx, LngIdx: lngIdx})
			}
			for lngIdx := lngStart2; lngIdx <= lngEnd2; lngIdx++ {
				keys = append(keys, CellKey{LatIdx: latIdx, LngIdx: lngIdx})
			}
		}
	} else {
		lngStart := int(math.Floor((bbox.MinLng + 180.0) / gridSize))
		lngEnd := int(math.Floor((bbox.MaxLng + 180.0) / gridSize))
		for latIdx := latStart; latIdx <= latEnd; latIdx++ {
			for lngIdx := lngStart; lngIdx <= lngEnd; lngIdx++ {
				keys = append(keys, CellKey{LatIdx: latIdx, LngIdx: lngIdx})
			}
		}
	}
	return keys
}

// LRU
type lruNode struct {
	key   string
	value []string
	prev  *lruNode
	next  *lruNode
}

type LRUCache struct {
	capacity int
	mu       sync.Mutex
	cache    map[string]*lruNode
	head     *lruNode
	tail     *lruNode
}

func NewLRUCache(capacity int) *LRUCache {
	return &LRUCache{
		capacity: capacity,
		cache:    make(map[string]*lruNode),
	}
}

func (c *LRUCache) moveToFront(n *lruNode) {
	if c.head == n {
		return
	}
	if n.prev != nil {
		n.prev.next = n.next
	}
	if n.next != nil {
		n.next.prev = n.prev
	}
	if c.tail == n {
		c.tail = n.prev
	}
	n.prev = nil
	n.next = c.head
	if c.head != nil {
		c.head.prev = n
	}
	c.head = n
	if c.tail == nil {
		c.tail = n
	}
}

func (c *LRUCache) Get(key string) ([]string, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if n, ok := c.cache[key]; ok {
		c.moveToFront(n)
		return n.value, true
	}
	return nil, false
}

func (c *LRUCache) Put(key string, value []string) {
	if c.capacity <= 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if n, ok := c.cache[key]; ok {
		cp := make([]string, len(value))
		copy(cp, value)
		n.value = cp
		c.moveToFront(n)
		return
	}
	cp := make([]string, len(value))
	copy(cp, value)
	n := &lruNode{key: key, value: cp}
	c.cache[key] = n
	n.next = c.head
	if c.head != nil {
		c.head.prev = n
	}
	c.head = n
	if c.tail == nil {
		c.tail = n
	}
	if len(c.cache) > c.capacity {
		if c.tail != nil {
			del := c.tail
			if del.prev != nil {
				del.prev.next = nil
				c.tail = del.prev
			} else {
				c.head = nil
				c.tail = nil
			}
			delete(c.cache, del.key)
		}
	}
}

func (c *LRUCache) Size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.cache)
}

func (c *LRUCache) Clear() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.cache = make(map[string]*lruNode)
	c.head = nil
	c.tail = nil
}

func cacheKey(lat, lng float64) string {
	return strconv.FormatFloat(lat, 'f', 6, 64) + "," + strconv.FormatFloat(lng, 'f', 6, 64)
}

type Server struct {
	mu             sync.RWMutex
	db             map[string]Geofence
	bboxes         map[string]BBox
	grid           map[CellKey][]string
	gridSize       float64
	cache          *LRUCache
	totalQueries   int64
	cacheHits      int64
	totalLatencyNs int64
	dbPath         string
}

func NewServer(db DB, gridSize float64, cacheSize int, dbPath string) *Server {
	s := &Server{
		db:       db,
		bboxes:   make(map[string]BBox, len(db)),
		grid:     make(map[CellKey][]string),
		gridSize: gridSize,
		cache:    NewLRUCache(cacheSize),
		dbPath:   dbPath,
	}
	for id, g := range db {
		bbox := computeBBox(g.Polygon)
		s.bboxes[id] = bbox
		for _, key := range gridCellsForBBox(bbox, gridSize) {
			s.grid[key] = append(s.grid[key], id)
		}
	}
	for k, ids := range s.grid {
		sort.Strings(ids)
		s.grid[k] = ids
	}
	return s
}

func (s *Server) getCellForPoint(lat, lng float64) CellKey {
	latIdx := int(math.Floor((lat + 90.0) / s.gridSize))
	lngIdx := int(math.Floor((lng + 180.0) / s.gridSize))
	return CellKey{LatIdx: latIdx, LngIdx: lngIdx}
}

func (s *Server) lookupPointInternalLocked(lat, lng float64) []string {
	cell := s.getCellForPoint(lat, lng)
	candidates := s.grid[cell]
	if len(candidates) == 0 {
		return []string{}
	}
	matching := make([]string, 0, 2)
	for _, id := range candidates {
		bbox := s.bboxes[id]
		if !pointInBBox(lat, lng, bbox) {
			continue
		}
		g := s.db[id]
		if pointInPolygon(lat, lng, g.Polygon) {
			matching = append(matching, id)
		}
	}
	return matching
}

func (s *Server) lookupPointInternal(lat, lng float64) []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lookupPointInternalLocked(lat, lng)
}

func (s *Server) lookupWithCache(lat, lng float64) (result []string, isHit bool, durationNs int64) {
	key := cacheKey(lat, lng)
	start := time.Now()
	s.mu.RLock()
	if s.cache.capacity > 0 {
		if val, ok := s.cache.Get(key); ok {
			durationNs = time.Since(start).Nanoseconds()
			s.mu.RUnlock()
			return val, true, durationNs
		}
	}
	res := s.lookupPointInternalLocked(lat, lng)
	durationNs = time.Since(start).Nanoseconds()
	if s.cache.capacity > 0 {
		s.cache.Put(key, res)
	}
	s.mu.RUnlock()
	return res, false, durationNs
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
	verboseStr := r.URL.Query().Get("verbose")
	verbose := false
	if verboseStr != "" {
		vl := strings.ToLower(verboseStr)
		if vl == "true" || vl == "1" || vl == "t" {
			verbose = true
		}
	}

	resultIDs, isHit, durNs := s.lookupWithCache(lat, lng)
	atomic.AddInt64(&s.totalQueries, 1)
	if isHit {
		atomic.AddInt64(&s.cacheHits, 1)
	}
	atomic.AddInt64(&s.totalLatencyNs, durNs)

	w.Header().Set("Content-Type", "application/json")
	if verbose {
		s.mu.RLock()
		objs := make([]Geofence, 0, len(resultIDs))
		for _, id := range resultIDs {
			if g, ok := s.db[id]; ok {
				objs = append(objs, g)
			}
		}
		s.mu.RUnlock()
		if objs == nil {
			objs = make([]Geofence, 0)
		}
		resp := map[string]interface{}{
			"lat":       lat,
			"lng":       lng,
			"geofences": objs,
			"count":     len(objs),
		}
		json.NewEncoder(w).Encode(resp)
	} else {
		if resultIDs == nil {
			resultIDs = make([]string, 0)
		}
		resp := map[string]interface{}{
			"lat":       lat,
			"lng":       lng,
			"geofences": resultIDs,
			"count":     len(resultIDs),
		}
		json.NewEncoder(w).Encode(resp)
	}
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
	var totalDur int64
	var hitCount int64
	var qCount int64
	for i, pt := range req.Points {
		geofs, isHit, dur := s.lookupWithCache(pt.Lat, pt.Lng)
		if geofs == nil {
			geofs = make([]string, 0)
		}
		results[i] = map[string]interface{}{
			"lat":       pt.Lat,
			"lng":       pt.Lng,
			"geofences": geofs,
		}
		totalDur += dur
		if isHit {
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

func (s *Server) handleGeofencesList(w http.ResponseWriter, r *http.Request) {
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

func (s *Server) handleGeofencesSingle(w http.ResponseWriter, r *http.Request, id string) {
	if r.Method == "GET" {
		s.mu.RLock()
		g, ok := s.db[id]
		s.mu.RUnlock()
		if !ok {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(g)
		return
	}
	if r.Method == "DELETE" {
		s.mu.Lock()
		_, ok := s.db[id]
		if !ok {
			s.mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
			return
		}
		// remove from grid (handle world-spanning and antimeridian crossing)
		oldBBox, hasBBox := s.bboxes[id]
		if hasBBox {
			for _, key := range gridCellsForBBox(oldBBox, s.gridSize) {
				ids := s.grid[key]
				newIds := make([]string, 0, len(ids))
				for _, gid := range ids {
					if gid != id {
						newIds = append(newIds, gid)
					}
				}
				if len(newIds) == 0 {
					delete(s.grid, key)
				} else {
					s.grid[key] = newIds
				}
			}
		}
		delete(s.db, id)
		delete(s.bboxes, id)
		s.cache.Clear()
		// persist
		dbCopy := make(DB, len(s.db))
		for k, v := range s.db {
			dbCopy[k] = v
		}
		s.mu.Unlock()
		if err := atomicWriteDBErr(s.dbPath, dbCopy); err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": fmt.Sprintf("failed to persist: %v", err)})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"deleted": id})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(405)
	json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
}

func (s *Server) handleGeofencesPost(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(405)
		json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
		return
	}
	var g Geofence
	if err := json.NewDecoder(r.Body).Decode(&g); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}
	// validate ID
	if !idRegex.MatchString(g.ID) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid id"})
		return
	}
	trimmedName := strings.TrimSpace(g.Name)
	if trimmedName == "" || len(trimmedName) > 128 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid name"})
		return
	}
	g.Name = trimmedName
	if err := validatePolygon(g.Polygon); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": fmt.Sprintf("invalid polygon: %v", err)})
		return
	}
	s.mu.Lock()
	// remove old if exists (handle world-spanning and antimeridian)
	if oldBBox, ok := s.bboxes[g.ID]; ok {
		for _, key := range gridCellsForBBox(oldBBox, s.gridSize) {
			ids := s.grid[key]
			newIds := make([]string, 0, len(ids))
			for _, gid := range ids {
				if gid != g.ID {
					newIds = append(newIds, gid)
				}
			}
			if len(newIds) == 0 {
				delete(s.grid, key)
			} else {
				s.grid[key] = newIds
			}
		}
	}
	s.db[g.ID] = g
	bbox := computeBBox(g.Polygon)
	s.bboxes[g.ID] = bbox
	for _, key := range gridCellsForBBox(bbox, s.gridSize) {
		s.grid[key] = append(s.grid[key], g.ID)
		sort.Strings(s.grid[key])
	}
	s.cache.Clear()
	dbCopy := make(DB, len(s.db))
	for k, v := range s.db {
		dbCopy[k] = v
	}
	s.mu.Unlock()
	if err := atomicWriteDBErr(s.dbPath, dbCopy); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(500)
		json.NewEncoder(w).Encode(map[string]string{"error": fmt.Sprintf("failed to persist: %v", err)})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(201)
	json.NewEncoder(w).Encode(g)
}

func (s *Server) handleGeofences(w http.ResponseWriter, r *http.Request) {
	// router for /geofences
	if r.URL.Path == "/geofences" || r.URL.Path == "/geofences/" {
		if r.Method == "GET" {
			s.handleGeofencesList(w, r)
			return
		}
		if r.Method == "POST" {
			s.handleGeofencesPost(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(405)
		json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
		return
	}
	// /geofences/{id}
	if strings.HasPrefix(r.URL.Path, "/geofences/") {
		id := strings.TrimPrefix(r.URL.Path, "/geofences/")
		id = strings.Trim(id, "/")
		// id may contain url encoding? Assume plain
		if id == "" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
			return
		}
		s.handleGeofencesSingle(w, r, id)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(404)
	json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
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
	hitRate := 0.0
	if totalQ > 0 {
		hitRate = float64(hits) / float64(totalQ)
	}

	resp := map[string]interface{}{
		"total_geofences": totalGeof,
		"total_queries":   totalQ,
		"cache_hits":      hits,
		"cache_size":      cacheSize,
		"avg_latency_ms":  avgMs,
		"index_cells":     indexCells,
		"cache_hit_rate":  hitRate,
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
	points, err := parsePolygonString(polygonStr)
	if err != nil {
		printErrExit(2, "invalid polygon: %v", err)
	}
	if err := validatePolygon(points); err != nil {
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
	matchingIDs := make([]string, 0)
	matchingGeofences := make([]Geofence, 0)
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
		if matchingGeofences == nil {
			matchingGeofences = make([]Geofence, 0)
		}
		out, _ := json.Marshal(matchingGeofences)
		fmt.Println(string(out))
	} else {
		if matchingIDs == nil {
			matchingIDs = make([]string, 0)
		}
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
	if n := runtime.NumCPU(); n > 1 {
		runtime.GOMAXPROCS(n)
	} else {
		runtime.GOMAXPROCS(4)
	}
	server := NewServer(db, gridSize, cacheSize, dbPath)

	mux := http.NewServeMux()
	mux.HandleFunc("/lookup", server.handleLookup)
	mux.HandleFunc("/lookup/batch", server.handleBatch)
	mux.HandleFunc("/geofences", server.handleGeofences)
	mux.HandleFunc("/geofences/", server.handleGeofences)
	mux.HandleFunc("/stats", server.handleStats)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(404)
		json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
	})

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Printf("serving on :%d\n", port)
	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	if err := srv.ListenAndServe(); err != nil {
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
