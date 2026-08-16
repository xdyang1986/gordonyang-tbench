#!/bin/bash
set -e
mkdir -p /app/src
cat > /app/src/go.mod <<'GOMOD'
module locationservice

go 1.22
GOMOD
cat > /app/src/main.go <<'GOMAIN'
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Point struct {
	Lat float64 `json:"lat"`
	Lng float64 `json:"lng"`
}

type HistoryEntry struct {
	Lat         float64 `json:"lat"`
	Lng         float64 `json:"lng"`
	TimestampMs int64   `json:"timestamp_ms"`
	Accuracy    float64 `json:"accuracy"`
	Speed       float64 `json:"speed"`
	Heading     float64 `json:"heading"`
}

type Location struct {
	VehicleID      string         `json:"vehicle_id"`
	Lat            float64        `json:"lat"`
	Lng            float64        `json:"lng"`
	TimestampMs    int64          `json:"timestamp_ms"`
	Accuracy       float64        `json:"accuracy"`
	Speed          float64        `json:"speed"`
	Heading        float64        `json:"heading"`
	TotalDistanceM float64        `json:"total_distance_m"`
	History        []HistoryEntry `json:"history,omitempty"`
}

type Zone struct {
	ID      string  `json:"id"`
	Polygon []Point `json:"polygon,omitempty"`
}

type RoadEntry struct {
	ID     string  `json:"id"`
	Points []Point `json:"points,omitempty"`
}

var vehicleIDRegex = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func exitPrint(code int, msg string, isErr bool) {
	if msg != "" {
		if isErr {
			fmt.Fprintln(os.Stderr, msg)
		} else {
			fmt.Fprintln(os.Stdout, msg)
		}
	}
	os.Exit(code)
}

func isInvalidFloat(f float64) bool {
	return math.IsNaN(f) || math.IsInf(f, 0)
}

func parseFloatArg(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	low := strings.ToLower(s)
	if low == "nan" || low == "+nan" || low == "-nan" || low == "inf" || low == "+inf" || low == "-inf" || low == "infinity" || low == "+infinity" || low == "-infinity" {
		return 0, false
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false
	}
	if isInvalidFloat(f) {
		return 0, false
	}
	return f, true
}

func parseIntArg(s string) (int64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

func haversine(lat1, lng1, lat2, lng2 float64) float64 {
	R := 6371000.0
	phi1 := lat1 * math.Pi / 180
	phi2 := lat2 * math.Pi / 180
	dphi := (lat2 - lat1) * math.Pi / 180
	dlambda := (lng2 - lng1) * math.Pi / 180
	a := math.Sin(dphi/2)*math.Sin(dphi/2) + math.Cos(phi1)*math.Cos(phi2)*math.Sin(dlambda/2)*math.Sin(dlambda/2)
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	return R * c
}

func pointOnSegment(lat, lng float64, a, b Point) bool {
	dx := b.Lng - a.Lng
	dy := b.Lat - a.Lat
	dxp := lng - a.Lng
	dyp := lat - a.Lat
	cross := dx*dyp - dy*dxp
	if math.Abs(cross) > 1e-9 {
		return false
	}
	if math.Abs(dx) < 1e-12 && math.Abs(dy) < 1e-12 {
		return math.Abs(dxp) < 1e-9 && math.Abs(dyp) < 1e-9
	}
	t := 0.0
	if math.Abs(dx) >= math.Abs(dy) && math.Abs(dx) > 1e-12 {
		t = dxp / dx
	} else if math.Abs(dy) > 1e-12 {
		t = dyp / dy
	}
	if t < -1e-9 || t > 1+1e-9 {
		return false
	}
	minLat := math.Min(a.Lat, b.Lat) - 1e-9
	maxLat := math.Max(a.Lat, b.Lat) + 1e-9
	minLng := math.Min(a.Lng, b.Lng) - 1e-9
	maxLng := math.Max(a.Lng, b.Lng) + 1e-9
	if lat < minLat || lat > maxLat || lng < minLng || lng > maxLng {
		return false
	}
	return true
}

func pointInPolygon(lat, lng float64, poly []Point) bool {
	if len(poly) < 3 {
		return false
	}
	for i := 0; i < len(poly); i++ {
		j := (i + 1) % len(poly)
		if pointOnSegment(lat, lng, poly[i], poly[j]) {
			return true
		}
	}
	inside := false
	j := len(poly) - 1
	for i := 0; i < len(poly); i++ {
		xi := poly[i].Lng
		yi := poly[i].Lat
		xj := poly[j].Lng
		yj := poly[j].Lat
		if (yi > lat) != (yj > lat) {
			if yj != yi {
				xinters := (xj-xi)*(lat-yi)/(yj-yi) + xi
				if lng <= xinters+1e-9 {
					inside = !inside
				}
			}
		}
		j = i
	}
	return inside
}

func validatePoint(p Point) bool {
	if isInvalidFloat(p.Lat) || isInvalidFloat(p.Lng) {
		return false
	}
	if p.Lat < -90 || p.Lat > 90 || p.Lng < -180 || p.Lng > 180 {
		return false
	}
	return true
}

func loadZones(path string) ([]Zone, error) {
	if path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return []Zone{}, nil
	}
	// Check for disallowed keys to keep spec simple: if file contains holes, center, radius_m, active_from/to, treat as invalid for step1
	low := strings.ToLower(string(data))
	if strings.Contains(low, "\"holes\"") || strings.Contains(low, "\"center\"") || strings.Contains(low, "\"radius_m\"") || strings.Contains(low, "\"active_from\"") || strings.Contains(low, "\"active_to\"") {
		return nil, fmt.Errorf("step1 zones must be polygon only, no holes/circles/time")
	}
	var zones []Zone
	if err := json.Unmarshal(data, &zones); err != nil {
		return nil, fmt.Errorf("invalid zones json: %w", err)
	}
	for _, z := range zones {
		if strings.TrimSpace(z.ID) == "" {
			return nil, fmt.Errorf("zone id empty")
		}
		if len(z.Polygon) < 3 {
			return nil, fmt.Errorf("zone %s polygon <3", z.ID)
		}
		for _, pt := range z.Polygon {
			if !validatePoint(pt) {
				return nil, fmt.Errorf("zone %s invalid point", z.ID)
			}
		}
	}
	return zones, nil
}

func isInsideAnyZone(lat, lng float64, zones []Zone) (bool, string) {
	for _, z := range zones {
		if pointInPolygon(lat, lng, z.Polygon) {
			return true, z.ID
		}
	}
	return false, ""
}

func loadRoads(path string) ([]RoadEntry, error) {
	if path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return nil, fmt.Errorf("empty roads")
	}
	var entries []RoadEntry
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil, fmt.Errorf("invalid roads json: %w", err)
	}
	for _, e := range entries {
		if strings.TrimSpace(e.ID) == "" {
			return nil, fmt.Errorf("road id empty")
		}
		if len(e.Points) < 2 {
			return nil, fmt.Errorf("road %s points <2", e.ID)
		}
		for _, pt := range e.Points {
			if !validatePoint(pt) {
				return nil, fmt.Errorf("road %s invalid point", e.ID)
			}
		}
	}
	return entries, nil
}

func latLngToXY(lat, lng, latRef float64) (x, y float64) {
	R := 6371000.0
	latRad := lat * math.Pi / 180
	lngRad := lng * math.Pi / 180
	latRefRad := latRef * math.Pi / 180
	x = R * lngRad * math.Cos(latRefRad)
	y = R * latRad
	return
}

func xyToLatLng(x, y, latRef float64) (lat, lng float64) {
	R := 6371000.0
	latRefRad := latRef * math.Pi / 180
	lat = y / R * 180 / math.Pi
	cosRef := math.Cos(latRefRad)
	if math.Abs(cosRef) < 1e-12 {
		cosRef = 1e-12
	}
	lng = x / (R * cosRef) * 180 / math.Pi
	return
}

func closestPointOnSegment(px, py, x1, y1, x2, y2 float64) (cx, cy, dist, t float64) {
	dx := x2 - x1
	dy := y2 - y1
	d2 := dx*dx + dy*dy
	if d2 < 1e-12 {
		cx = x1
		cy = y1
		dist = math.Hypot(px-x1, py-y1)
		t = 0
		return
	}
	t = ((px-x1)*dx + (py-y1)*dy) / d2
	if t < 0 {
		t = 0
	} else if t > 1 {
		t = 1
	}
	cx = x1 + t*dx
	cy = y1 + t*dy
	dist = math.Hypot(px-cx, py-cy)
	return
}

type SnapResult struct {
	Snapped bool
	RoadID  string
	DistM   float64
}

func snapToRoads(lat, lng float64, roads []RoadEntry) SnapResult {
	if len(roads) == 0 {
		return SnapResult{Snapped: false}
	}
	latRef := lat
	px, py := latLngToXY(lat, lng, latRef)
	bestDist := math.MaxFloat64
	var best SnapResult
	for _, road := range roads {
		pts := road.Points
		for i := 0; i < len(pts)-1; i++ {
			p1 := pts[i]
			p2 := pts[i+1]
			x1, y1 := latLngToXY(p1.Lat, p1.Lng, latRef)
			x2, y2 := latLngToXY(p2.Lat, p2.Lng, latRef)
			cx, cy, dist, _ := closestPointOnSegment(px, py, x1, y1, x2, y2)
			if dist < bestDist {
				bestDist = dist
				best = SnapResult{Snapped: true, RoadID: road.ID, DistM: dist}
				_ = cx
				_ = cy
			}
		}
	}
	if best.Snapped && bestDist <= 50.0+1e-9 {
		return best
	}
	return SnapResult{Snapped: false}
}

func backupCorrupt(path string, raw []byte) {
	backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
	_ = os.WriteFile(backupPath, raw, 0644)
}

func loadDB(path string) (map[string]Location, error) {
	db := make(map[string]Location)
	if path == "" {
		return db, nil
	}
	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return db, nil
		}
		return nil, err
	}
	if info.Size() == 0 {
		return db, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return db, nil
	}
	if trimmed == "null" {
		backupCorrupt(path, data)
		return nil, fmt.Errorf("corrupt")
	}
	var raw json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		backupCorrupt(path, data)
		return nil, fmt.Errorf("corrupt")
	}
	var m map[string]Location
	if err := json.Unmarshal(data, &m); err != nil {
		backupCorrupt(path, data)
		return nil, fmt.Errorf("corrupt")
	}
	if len(trimmed) > 0 && trimmed[0] == '[' {
		backupCorrupt(path, data)
		return nil, fmt.Errorf("corrupt")
	}
	return m, nil
}

func saveDB(path string, db map[string]Location) error {
	if path == "" {
		return fmt.Errorf("empty path")
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	data, err := json.Marshal(db)
	if err != nil {
		return err
	}
	tmp := fmt.Sprintf("%s.tmp.%d", path, os.Getpid())
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if files, err := os.ReadDir(dir); err == nil {
		prefix := filepath.Base(path) + ".tmp."
		for _, f := range files {
			if strings.HasPrefix(f.Name(), prefix) {
				_ = os.Remove(filepath.Join(dir, f.Name()))
			}
		}
	}
	return nil
}

func parseGlobalDBFlag(args []string) (dbPath string, remaining []string) {
	for i := 0; i < len(args); i++ {
		if args[i] == "--db" {
			if i+1 < len(args) {
				dbPath = args[i+1]
				i++
			} else {
				exitPrint(2, "missing --db value", true)
			}
		} else if strings.HasPrefix(args[i], "--db=") {
			dbPath = args[i][5:]
		} else {
			remaining = append(remaining, args[i])
		}
	}
	return
}

func printHelp() {
	fmt.Println("locationctl help")
	fmt.Println("Commands: update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check")
	fmt.Println("Usage: locationctl --db <PATH> <command> [args] [flags]")
}

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		printHelp()
		return
	}
	dbPath, rem := parseGlobalDBFlag(args)
	if len(rem) == 0 {
		printHelp()
		return
	}
	first := rem[0]
	if first == "help" || first == "--help" || first == "-h" {
		printHelp()
		return
	}
	cmd := first
	cmdArgs := rem[1:]

	switch cmd {
	case "update":
		if len(cmdArgs) < 4 {
			exitPrint(2, "update requires <vehicle_id> <lat> <lng> <timestamp_ms>", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		lat, ok := parseFloatArg(cmdArgs[1])
		if !ok || lat < -90 || lat > 90 {
			exitPrint(2, "invalid lat", true)
		}
		lng, ok := parseFloatArg(cmdArgs[2])
		if !ok || lng < -180 || lng > 180 {
			exitPrint(2, "invalid lng", true)
		}
		ts, ok := parseIntArg(cmdArgs[3])
		if !ok || ts < 0 {
			exitPrint(2, "invalid timestamp", true)
		}
		acc := 10.0
		spd := 0.0
		hdg := 0.0
		zonesPath := ""
		for i := 4; i < len(cmdArgs); i++ {
			a := cmdArgs[i]
			if a == "--accuracy" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --accuracy value", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy", true)
				}
				acc = v
				i++
			} else if strings.HasPrefix(a, "--accuracy=") {
				v, ok := parseFloatArg(a[len("--accuracy="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy", true)
				}
				acc = v
			} else if a == "--speed" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --speed value", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed", true)
				}
				spd = v
				i++
			} else if strings.HasPrefix(a, "--speed=") {
				v, ok := parseFloatArg(a[len("--speed="):])
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed", true)
				}
				spd = v
			} else if a == "--heading" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --heading value", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok || v < 0 || v >= 360 {
					exitPrint(2, "invalid heading", true)
				}
				hdg = v
				i++
			} else if strings.HasPrefix(a, "--heading=") {
				v, ok := parseFloatArg(a[len("--heading="):])
				if !ok || v < 0 || v >= 360 {
					exitPrint(2, "invalid heading", true)
				}
				hdg = v
			} else if a == "--zones" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --zones value", true)
				}
				zonesPath = cmdArgs[i+1]
				i++
			} else if strings.HasPrefix(a, "--zones=") {
				zonesPath = a[len("--zones="):]
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		if zonesPath == "" {
			if _, err := os.Stat("/app/data/zones.json"); err == nil {
				zonesPath = "/app/data/zones.json"
			}
		}
		var zones []Zone
		if zonesPath != "" {
			z, err := loadZones(zonesPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		if len(zones) > 0 {
			ok, _ := isInsideAnyZone(lat, lng, zones)
			if !ok {
				fmt.Println("out_of_zone")
				os.Exit(3)
			}
		}
		if existing, ok := db[vid]; ok {
			if ts <= existing.TimestampMs {
				fmt.Println("stale")
				os.Exit(0)
			}
			dist := haversine(existing.Lat, existing.Lng, lat, lng)
			hist := existing.History
			sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
			hist = append(hist, HistoryEntry{Lat: lat, Lng: lng, TimestampMs: ts, Accuracy: acc, Speed: spd, Heading: hdg})
			if len(hist) > 10 {
				hist = hist[len(hist)-10:]
			}
			sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
			db[vid] = Location{VehicleID: vid, Lat: lat, Lng: lng, TimestampMs: ts, Accuracy: acc, Speed: spd, Heading: hdg, TotalDistanceM: existing.TotalDistanceM + dist, History: hist}
		} else {
			hist := []HistoryEntry{{Lat: lat, Lng: lng, TimestampMs: ts, Accuracy: acc, Speed: spd, Heading: hdg}}
			db[vid] = Location{VehicleID: vid, Lat: lat, Lng: lng, TimestampMs: ts, Accuracy: acc, Speed: spd, Heading: hdg, TotalDistanceM: 0, History: hist}
		}
		if err := saveDB(dbPath, db); err != nil {
			exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
		}
		loc := db[vid]
		out := map[string]interface{}{"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs, "accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading, "total_distance_m": loc.TotalDistanceM}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	case "get":
		if len(cmdArgs) < 1 {
			exitPrint(2, "get requires <vehicle_id>", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		verbose := false
		for _, a := range cmdArgs[1:] {
			if a == "--verbose" {
				verbose = true
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		loc, ok := db[vid]
		if !ok {
			exitPrint(3, "not found", true)
		}
		if verbose {
			b, _ := json.Marshal(loc)
			fmt.Println(string(b))
		} else {
			out := map[string]interface{}{"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs, "accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading, "total_distance_m": loc.TotalDistanceM}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
		}

	case "list":
		var since, until *int64
		limit := -1
		hasLimit := false
		offset := 0
		zonesPath := ""
		roadsPath := ""
		i := 0
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--since" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --since value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid since", true)
				}
				since = &v
				i += 2
			} else if strings.HasPrefix(a, "--since=") {
				v, ok := parseIntArg(a[len("--since="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid since", true)
				}
				since = &v
				i++
			} else if a == "--until" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --until value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid until", true)
				}
				until = &v
				i += 2
			} else if strings.HasPrefix(a, "--until=") {
				v, ok := parseIntArg(a[len("--until="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid until", true)
				}
				until = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i++
			} else if a == "--offset" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --offset value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i++
			} else if a == "--zones" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --zones value", true)
				}
				zonesPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--zones=") {
				zonesPath = a[len("--zones="):]
				i++
			} else if a == "--roads" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --roads value", true)
				}
				roadsPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--roads=") {
				roadsPath = a[len("--roads="):]
				i++
			} else if strings.HasPrefix(a, "--now") {
				// Step1 simplified: accept --now but ignore for zones (kept for compat with stale tests that don't use now)
				// Consume value if --now <val> or --now=<val>
				if a == "--now" {
					i += 2
				} else {
					i++
				}
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		if since != nil && until != nil && *since > *until {
			exitPrint(2, "since > until", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		var zones []Zone
		if zonesPath != "" {
			z, err := loadZones(zonesPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		var roads []RoadEntry
		if roadsPath != "" {
			r, err := loadRoads(roadsPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid roads file: %v", err), true)
			}
			roads = r
		}
		var list []Location
		for _, loc := range db {
			if since != nil && loc.TimestampMs < *since {
				continue
			}
			if until != nil && loc.TimestampMs > *until {
				continue
			}
			if zonesPath != "" && len(zones) > 0 {
				ok, _ := isInsideAnyZone(loc.Lat, loc.Lng, zones)
				if !ok {
					continue
				}
			}
			if roadsPath != "" {
				snap := snapToRoads(loc.Lat, loc.Lng, roads)
				if !snap.Snapped {
					continue
				}
			}
			list = append(list, loc)
		}
		sort.Slice(list, func(i, j int) bool { return list[i].VehicleID < list[j].VehicleID })
		if offset > len(list) {
			list = []Location{}
		} else {
			list = list[offset:]
		}
		if hasLimit {
			if limit == 0 {
				list = []Location{}
			} else if limit > 0 && limit < len(list) {
				list = list[:limit]
			}
		}
		type outLoc struct {
			VehicleID      string  `json:"vehicle_id"`
			Lat            float64 `json:"lat"`
			Lng            float64 `json:"lng"`
			TimestampMs    int64   `json:"timestamp_ms"`
			Accuracy       float64 `json:"accuracy"`
			Speed          float64 `json:"speed"`
			Heading        float64 `json:"heading"`
			TotalDistanceM float64 `json:"total_distance_m"`
		}
		var out []outLoc
		for _, l := range list {
			out = append(out, outLoc{l.VehicleID, l.Lat, l.Lng, l.TimestampMs, l.Accuracy, l.Speed, l.Heading, l.TotalDistanceM})
		}
		if out == nil {
			out = []outLoc{}
		}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	case "near":
		var latQ, lngQ, radius float64
		hasLat, hasLng, hasRadius := false, false, false
		var accMax *float64
		var speedMin *float64
		limit := -1
		hasLimit := false
		offset := 0
		var nowTs *int64
		includeStale := false
		zonesPath := ""
		roadsPath := ""
		i := 0
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--lat" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --lat", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok {
					exitPrint(2, "invalid lat", true)
				}
				latQ = v
				hasLat = true
				i += 2
			} else if strings.HasPrefix(a, "--lat=") {
				v, ok := parseFloatArg(a[len("--lat="):])
				if !ok {
					exitPrint(2, "invalid lat", true)
				}
				latQ = v
				hasLat = true
				i++
			} else if a == "--lng" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --lng", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok {
					exitPrint(2, "invalid lng", true)
				}
				lngQ = v
				hasLng = true
				i += 2
			} else if strings.HasPrefix(a, "--lng=") {
				v, ok := parseFloatArg(a[len("--lng="):])
				if !ok {
					exitPrint(2, "invalid lng", true)
				}
				lngQ = v
				hasLng = true
				i++
			} else if a == "--radius" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --radius", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok {
					exitPrint(2, "invalid radius", true)
				}
				radius = v
				hasRadius = true
				i += 2
			} else if strings.HasPrefix(a, "--radius=") {
				v, ok := parseFloatArg(a[len("--radius="):])
				if !ok {
					exitPrint(2, "invalid radius", true)
				}
				radius = v
				hasRadius = true
				i++
			} else if a == "--accuracy-max" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --accuracy-max", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy-max", true)
				}
				accMax = &v
				i += 2
			} else if strings.HasPrefix(a, "--accuracy-max=") {
				v, ok := parseFloatArg(a[len("--accuracy-max="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy-max", true)
				}
				accMax = &v
				i++
			} else if a == "--speed-min" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --speed-min", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1])
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed-min", true)
				}
				speedMin = &v
				i += 2
			} else if strings.HasPrefix(a, "--speed-min=") {
				v, ok := parseFloatArg(a[len("--speed-min="):])
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed-min", true)
				}
				speedMin = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i++
			} else if a == "--offset" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --offset value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i++
			} else if a == "--now" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --now value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i += 2
			} else if strings.HasPrefix(a, "--now=") {
				v, ok := parseIntArg(a[len("--now="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i++
			} else if a == "--include-stale" {
				includeStale = true
				i++
			} else if a == "--zones" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --zones value", true)
				}
				zonesPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--zones=") {
				zonesPath = a[len("--zones="):]
				i++
			} else if a == "--roads" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --roads value", true)
				}
				roadsPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--roads=") {
				roadsPath = a[len("--roads="):]
				i++
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		if !hasLat || !hasLng || !hasRadius {
			exitPrint(2, "near requires --lat --lng --radius", true)
		}
		if latQ < -90 || latQ > 90 || lngQ < -180 || lngQ > 180 || radius < 0 || radius > 50000 {
			exitPrint(2, "invalid lat/lng/radius", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		var zones []Zone
		if zonesPath != "" {
			z, err := loadZones(zonesPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		var roads []RoadEntry
		if roadsPath != "" {
			r, err := loadRoads(roadsPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid roads file: %v", err), true)
			}
			roads = r
		}
		type nearEntry struct {
			Location
			Distance float64 `json:"distance_m"`
		}
		var entries []nearEntry
		for _, loc := range db {
			if accMax != nil && loc.Accuracy > *accMax {
				continue
			}
			if speedMin != nil && loc.Speed < *speedMin {
				continue
			}
			if nowTs != nil && !includeStale {
				age := *nowTs - loc.TimestampMs
				if age < 0 {
					age = 0
				}
				if age > 30000 {
					continue
				}
			}
			if zonesPath != "" && len(zones) > 0 {
				ok, _ := isInsideAnyZone(loc.Lat, loc.Lng, zones)
				if !ok {
					continue
				}
			}
			if roadsPath != "" {
				snap := snapToRoads(loc.Lat, loc.Lng, roads)
				if !snap.Snapped {
					continue
				}
			}
			d := haversine(latQ, lngQ, loc.Lat, loc.Lng)
			if d > radius+1e-6 {
				continue
			}
			entries = append(entries, nearEntry{Location: loc, Distance: d})
		}
		sort.Slice(entries, func(i, j int) bool {
			if math.Abs(entries[i].Distance-entries[j].Distance) > 1e-6 {
				return entries[i].Distance < entries[j].Distance
			}
			return entries[i].VehicleID < entries[j].VehicleID
		})
		if offset > len(entries) {
			entries = []nearEntry{}
		} else {
			entries = entries[offset:]
		}
		if hasLimit {
			if limit == 0 {
				entries = []nearEntry{}
			} else if limit > 0 && limit < len(entries) {
				entries = entries[:limit]
			}
		}
		var out []map[string]interface{}
		for _, e := range entries {
			out = append(out, map[string]interface{}{
				"vehicle_id": e.VehicleID, "lat": e.Lat, "lng": e.Lng, "timestamp_ms": e.TimestampMs,
				"accuracy": e.Accuracy, "speed": e.Speed, "heading": e.Heading,
				"total_distance_m": e.TotalDistanceM, "distance_m": e.Distance,
			})
		}
		if out == nil {
			out = []map[string]interface{}{}
		}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	case "track":
		if len(cmdArgs) < 3 {
			exitPrint(2, "track requires <vehicle_id> --from --to", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		var from, to *int64
		limit := -1
		hasLimit := false
		offset := 0
		i := 1
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--from" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --from", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid from", true)
				}
				from = &v
				i += 2
			} else if strings.HasPrefix(a, "--from=") {
				v, ok := parseIntArg(a[len("--from="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid from", true)
				}
				from = &v
				i++
			} else if a == "--to" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --to", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid to", true)
				}
				to = &v
				i += 2
			} else if strings.HasPrefix(a, "--to=") {
				v, ok := parseIntArg(a[len("--to="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid to", true)
				}
				to = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i++
			} else if a == "--offset" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --offset value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):])
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i++
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		if from == nil || to == nil {
			exitPrint(2, "track requires --from and --to", true)
		}
		if *from > *to {
			exitPrint(2, "from > to", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		loc, ok := db[vid]
		if !ok {
			exitPrint(3, "not found", true)
		}
		hist := loc.History
		var filtered []HistoryEntry
		for _, h := range hist {
			if h.TimestampMs >= *from && h.TimestampMs <= *to {
				filtered = append(filtered, h)
			}
		}
		sort.Slice(filtered, func(i, j int) bool { return filtered[i].TimestampMs < filtered[j].TimestampMs })
		if offset > len(filtered) {
			filtered = []HistoryEntry{}
		} else {
			filtered = filtered[offset:]
		}
		if hasLimit {
			if limit == 0 {
				filtered = []HistoryEntry{}
			} else if limit > 0 && limit < len(filtered) {
				filtered = filtered[:limit]
			}
		}
		if filtered == nil {
			filtered = []HistoryEntry{}
		}
		b, _ := json.Marshal(filtered)
		fmt.Println(string(b))

	case "distance":
		if len(cmdArgs) < 1 {
			exitPrint(2, "distance requires <vehicle_id>", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		loc, ok := db[vid]
		if !ok {
			exitPrint(3, "not found", true)
		}
		out := map[string]interface{}{"vehicle_id": loc.VehicleID, "total_distance_m": loc.TotalDistanceM}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	case "delete":
		if len(cmdArgs) < 1 {
			exitPrint(2, "delete requires <vehicle_id>", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		delete(db, vid)
		if err := saveDB(dbPath, db); err != nil {
			exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
		}
		fmt.Println("deleted")

	case "stats":
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		live := len(db)
		totalUpdates := 0
		for _, loc := range db {
			totalUpdates += len(loc.History)
		}
		var totalDist float64
		var totalAcc float64
		for _, loc := range db {
			totalDist += loc.TotalDistanceM
			totalAcc += loc.Accuracy
		}
		avgAcc := 0.0
		if live > 0 {
			avgAcc = totalAcc / float64(live)
		}
		out := map[string]interface{}{"live": live, "total_updates": totalUpdates, "total_distance_m": totalDist, "avg_accuracy": avgAcc}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	case "batch":
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		scanner := bufio.NewScanner(os.Stdin)
		type batchOp struct {
			kind string
			vid  string
			lat  float64
			lng  float64
			ts   int64
			acc  float64
			spd  float64
			hdg  float64
		}
		var ops []batchOp
		for scanner.Scan() {
			line := scanner.Text()
			if strings.TrimSpace(line) == "" {
				continue
			}
			parts := strings.Split(line, "\t")
			if len(parts) < 2 {
				exitPrint(2, fmt.Sprintf("invalid batch line: %s", line), true)
			}
			switch parts[0] {
			case "update":
				if len(parts) < 5 || len(parts) > 8 {
					exitPrint(2, fmt.Sprintf("invalid update field count %d", len(parts)), true)
				}
				vid := parts[1]
				if !vehicleIDRegex.MatchString(vid) {
					exitPrint(2, fmt.Sprintf("invalid vehicle_id %s", vid), true)
				}
				lat, ok1 := parseFloatArg(parts[2])
				lng, ok2 := parseFloatArg(parts[3])
				ts, ok3 := parseIntArg(parts[4])
				if !ok1 || lat < -90 || lat > 90 {
					exitPrint(2, fmt.Sprintf("invalid lat %s", parts[2]), true)
				}
				if !ok2 || lng < -180 || lng > 180 {
					exitPrint(2, fmt.Sprintf("invalid lng %s", parts[3]), true)
				}
				if !ok3 || ts < 0 {
					exitPrint(2, fmt.Sprintf("invalid ts %s", parts[4]), true)
				}
				acc := 10.0
				spd := 0.0
				hdg := 0.0
				if len(parts) >= 6 {
					s := strings.TrimSpace(parts[5])
					if s != "" {
						v, ok := parseFloatArg(s)
						if !ok || v < 0 {
							exitPrint(2, fmt.Sprintf("invalid accuracy %s", s), true)
						}
						acc = v
					}
				}
				if len(parts) >= 7 {
					s := strings.TrimSpace(parts[6])
					if s != "" {
						v, ok := parseFloatArg(s)
						if !ok || v < 0 || v > 50 {
							exitPrint(2, fmt.Sprintf("invalid speed %s", s), true)
						}
						spd = v
					}
				}
				if len(parts) >= 8 {
					s := strings.TrimSpace(parts[7])
					if s != "" {
						v, ok := parseFloatArg(s)
						if !ok || v < 0 || v >= 360 {
							exitPrint(2, fmt.Sprintf("invalid heading %s", s), true)
						}
						hdg = v
					}
				}
				ops = append(ops, batchOp{kind: "update", vid: vid, lat: lat, lng: lng, ts: ts, acc: acc, spd: spd, hdg: hdg})
			case "delete":
				if len(parts) != 2 {
					exitPrint(2, fmt.Sprintf("invalid delete field count %d", len(parts)), true)
				}
				vid := parts[1]
				if !vehicleIDRegex.MatchString(vid) {
					exitPrint(2, fmt.Sprintf("invalid vehicle_id %s", vid), true)
				}
				ops = append(ops, batchOp{kind: "delete", vid: vid})
			default:
				exitPrint(2, fmt.Sprintf("unknown batch kind %s", parts[0]), true)
			}
		}
		var zones []Zone
		if _, err := os.Stat("/app/data/zones.json"); err == nil {
			z, err := loadZones("/app/data/zones.json")
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		for _, op := range ops {
			if op.kind == "update" && len(zones) > 0 {
				ok, _ := isInsideAnyZone(op.lat, op.lng, zones)
				if !ok {
					exitPrint(2, "out_of_zone in batch", true)
				}
			}
		}
		dbCopy := make(map[string]Location)
		for k, v := range db {
			dbCopy[k] = v
		}
		applied := 0
		for _, op := range ops {
			if op.kind == "delete" {
				delete(dbCopy, op.vid)
				applied++
				continue
			}
			if existing, ok := dbCopy[op.vid]; ok {
				if op.ts <= existing.TimestampMs {
					continue
				}
				dist := haversine(existing.Lat, existing.Lng, op.lat, op.lng)
				hist := existing.History
				sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
				hist = append(hist, HistoryEntry{Lat: op.lat, Lng: op.lng, TimestampMs: op.ts, Accuracy: op.acc, Speed: op.spd, Heading: op.hdg})
				if len(hist) > 10 {
					hist = hist[len(hist)-10:]
				}
				sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
				dbCopy[op.vid] = Location{VehicleID: op.vid, Lat: op.lat, Lng: op.lng, TimestampMs: op.ts, Accuracy: op.acc, Speed: op.spd, Heading: op.hdg, TotalDistanceM: existing.TotalDistanceM + dist, History: hist}
				applied++
			} else {
				hist := []HistoryEntry{{Lat: op.lat, Lng: op.lng, TimestampMs: op.ts, Accuracy: op.acc, Speed: op.spd, Heading: op.hdg}}
				dbCopy[op.vid] = Location{VehicleID: op.vid, Lat: op.lat, Lng: op.lng, TimestampMs: op.ts, Accuracy: op.acc, Speed: op.spd, Heading: op.hdg, TotalDistanceM: 0, History: hist}
				applied++
			}
		}
		if err := saveDB(dbPath, dbCopy); err != nil {
			exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
		}
		fmt.Printf("batch_ok %d\n", applied)

	case "clear":
		db := make(map[string]Location)
		if err := saveDB(dbPath, db); err != nil {
			exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
		}
		fmt.Println("cleared")

	case "geofence-check":
		if len(cmdArgs) < 2 {
			exitPrint(2, "geofence-check requires <lat> <lng>", true)
		}
		latF, ok1 := parseFloatArg(cmdArgs[0])
		lngF, ok2 := parseFloatArg(cmdArgs[1])
		if !ok1 || latF < -90 || latF > 90 {
			exitPrint(2, "invalid lat", true)
		}
		if !ok2 || lngF < -180 || lngF > 180 {
			exitPrint(2, "invalid lng", true)
		}
		zonesPath := ""
		i := 2
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--zones" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --zones value", true)
				}
				zonesPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--zones=") {
				zonesPath = a[len("--zones="):]
				i++
			} else if strings.HasPrefix(a, "--now") {
				if a == "--now" {
					i += 2
				} else {
					i++
				}
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		if zonesPath == "" {
			if _, err := os.Stat("/app/data/zones.json"); err == nil {
				zonesPath = "/app/data/zones.json"
			}
		}
		var zones []Zone
		if zonesPath != "" {
			z, err := loadZones(zonesPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		if len(zones) == 0 {
			out := map[string]interface{}{"inside": false, "zone_id": ""}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
			return
		}
		for _, z := range zones {
			if pointInPolygon(latF, lngF, z.Polygon) {
				out := map[string]interface{}{"inside": true, "zone_id": z.ID}
				b, _ := json.Marshal(out)
				fmt.Println(string(b))
				return
			}
		}
		out := map[string]interface{}{"inside": false, "zone_id": ""}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))

	default:
		exitPrint(2, fmt.Sprintf("unknown command %s", cmd), true)
	}
}
GOMAIN
