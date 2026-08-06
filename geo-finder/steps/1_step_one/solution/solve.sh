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
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
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
	trim := strings.TrimSpace(string(data))
	if strings.HasPrefix(trim, "[") {
		printErrExit(4, "corrupt DB: expected object, got array")
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
const eps = 1e-9
func pointsEqual(a, b Point) bool { return a.Lat == b.Lat && a.Lng == b.Lng }
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
	val := (q.Lng-p.Lng)*(r.Lat-p.Lat) - (q.Lat-p.Lat)*(r.Lng-p.Lng)
	if math.Abs(val) < eps {
		return 0
	}
	if val > 0 {
		return 1
	}
	return 2
}
func onSegment(p, q, r Point) bool {
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
	for i := 0; i < n; i++ {
		i2 := (i + 1) % n
		p1 := poly[i]
		q1 := poly[i2]
		if pointsEqual(p1, q1) {
			return true
		}
		for j := i + 1; j < n; j++ {
			j2 := (j + 1) % n
			if i == j {
				continue
			}
			if i2 == j || j2 == i || i == j2 || i2 == j2 {
				continue
			}
			p2 := poly[j]
			q2 := poly[j2]
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
	g := Geofence{ID: id, Name: trimmedName, Polygon: points}
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
	sort.Slice(matchingGeofences, func(i, j int) bool { return matchingGeofences[i].ID < matchingGeofences[j].ID })
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
func main() {
	dbPath, filtered := parseDBFlag()
	if dbPath == "" {
		printErrExit(2, "--db flag is required")
	}
	if len(filtered) == 0 {
		printErrExit(2, "command required: add, remove, list, lookup, clear")
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
	default:
		printErrExit(2, "unknown command %s", cmd)
	}
}

GOEOF

cd /app/src
go mod tidy
go build -o geofencectl .
echo "solved"
