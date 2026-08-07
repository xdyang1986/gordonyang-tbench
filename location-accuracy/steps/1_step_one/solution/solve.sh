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
	ID         string     `json:"id"`
	Polygon    []Point    `json:"polygon,omitempty"`
	Holes      [][]Point  `json:"holes,omitempty"`
	Center     *Point     `json:"center,omitempty"`
	RadiusM    *float64   `json:"radius_m,omitempty"`
	ActiveFrom *int64     `json:"active_from,omitempty"`
	ActiveTo   *int64     `json:"active_to,omitempty"`
}

type RoadEntry struct {
	ID     string  `json:"id"`
	Points []Point `json:"points,omitempty"`
	Start  *Point  `json:"start,omitempty"`
	End    *Point  `json:"end,omitempty"`
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

func parseFloatArg(s, name string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	// Reject NaN, Inf case-insensitive via explicit check before ParseFloat
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

func parseIntArg(s, name string) (int64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	// Must be integer, no float
	if strings.Contains(s, ".") || strings.Contains(strings.ToLower(s), "e") {
		// Could still be integer with e? But spec says integer string, reject float hex etc. We'll try ParseInt, it will fail for decimal.
	}
	// Reject hex etc by ensuring no letters except minus
	// Use ParseInt base 10
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

func normalizeLng(lng, ref float64) float64 {
	for lng < ref-180 {
		lng += 360
	}
	for lng > ref+180 {
		lng -= 360
	}
	return lng
}

func pointOnSegmentEdge(lat, lng float64, a, b Point, eps float64) bool {
	// Check if point is on segment a-b within eps for distance to line and within bounding box
	// Use equirectangular for small distances? For edge check, use simple bounding box plus area check.
	// Compute cross product in lng/lat space (approx)
	// For robustness, check if point close to segment using haversine? But for polygon edge test we want point on edge => inside.
	// We'll use: check if point is colinear with a-b and within bbox.
	// Use tolerance 1e-9 degrees ~ 0.1mm, but also for antimeridian normalized.
	// Normalize b lng relative to a
	bLngNorm := normalizeLng(b.Lng, a.Lng)
	pLngNorm := normalizeLng(lng, a.Lng)
	// Vector a->b: dx = bLngNorm - a.Lng, dy = b.Lat - a.Lat
	// Vector a->p: dxp = pLngNorm - a.Lng, dyp = lat - a.Lat
	dx := bLngNorm - a.Lng
	dy := b.Lat - a.Lat
	dxp := pLngNorm - a.Lng
	dyp := lat - a.Lat
	// Cross product
	cross := dx*dyp - dy*dxp
	if math.Abs(cross) > 1e-9 {
		return false
	}
	// Dot product to check within segment
	// If segment is point, check distance
	if math.Abs(dx) < 1e-12 && math.Abs(dy) < 1e-12 {
		return math.Abs(dxp) < 1e-9 && math.Abs(dyp) < 1e-9
	}
	// Projection
	if dx != 0 || dy != 0 {
		t := (dxp*dx + dyp*dy) / (dx*dx + dy*dy)
		if t < -1e-9 || t > 1+1e-9 {
			return false
		}
	}
	// Bounding box check with epsilon
	minLat := math.Min(a.Lat, b.Lat) - 1e-9
	maxLat := math.Max(a.Lat, b.Lat) + 1e-9
	minLng := math.Min(a.Lng, bLngNorm) - 1e-9
	maxLng := math.Max(a.Lng, bLngNorm) + 1e-9
	if lat < minLat || lat > maxLat {
		return false
	}
	if pLngNorm < minLng || pLngNorm > maxLng {
		return false
	}
	return true
}

func unwrapPolygon(poly []Point) []Point {
	if len(poly) == 0 {
		return poly
	}
	unwrapped := make([]Point, len(poly))
	unwrapped[0] = poly[0]
	for i := 1; i < len(poly); i++ {
		prev := unwrapped[i-1].Lng
		curr := poly[i].Lng
		// find curr equivalent closest to prev
		diff := curr - poly[i-1].Lng
		// Actually difference of original lnga? Use prev unwrapped vs raw curr
		// Choose k to minimize abs(curr+360k - prev)
		// Compute diff = curr - prev; adjust to [-180,180]
		d := curr - prev
		for d > 180 {
			d -= 360
			curr -= 360
		}
		for d < -180 {
			d += 360
			curr += 360
		}
		unwrapped[i] = Point{Lat: poly[i].Lat, Lng: curr}
		_ = diff
	}
	return unwrapped
}

func unwrapLngForQuery(qLng float64, refLng float64) float64 {
	// unwrap qLng to be within [ref-180, ref+180]
	for qLng < refLng-180 {
		qLng += 360
	}
	for qLng > refLng+180 {
		qLng -= 360
	}
	return qLng
}

func pointInPolygonSingle(lat, lng float64, poly []Point) bool {
	if len(poly) < 3 {
		return false
	}
	// Unwrap polygon to continuous longitudes
	unwrapped := unwrapPolygon(poly)
	// Unwrap query lng to near first point
	qLng := unwrapLngForQuery(lng, unwrapped[0].Lng)
	// Check on edge using unwrapped
	for i := 0; i < len(unwrapped); i++ {
		j := (i + 1) % len(unwrapped)
		if pointOnSegmentEdge(lat, qLng, unwrapped[i], unwrapped[j], 1e-9) {
			return true
		}
	}
	inside := false
	j := len(unwrapped) - 1
	for i := 0; i < len(unwrapped); i++ {
		xi := unwrapped[i].Lng
		yi := unwrapped[i].Lat
		xj := unwrapped[j].Lng
		yj := unwrapped[j].Lat
		if (yi > lat) != (yj > lat) {
			if yj != yi {
				xinters := (xj-xi)*(lat-yi)/(yj-yi) + xi
				if qLng <= xinters+1e-12 {
					inside = !inside
				}
			}
		}
		j = i
	}
	return inside
}

func pointInPolygonWithHoles(lat, lng float64, poly []Point, holes [][]Point) bool {
	if !pointInPolygonSingle(lat, lng, poly) {
		return false
	}
	for _, hole := range holes {
		if pointInPolygonSingle(lat, lng, hole) {
			return false
		}
	}
	return true
}

func validatePoint(p Point) bool {
	if isInvalidFloat(p.Lat) || isInvalidFloat(p.Lng) {
		return false
	}
	if p.Lat < -90 || p.Lat > 90 {
		return false
	}
	if p.Lng < -180 || p.Lng > 180 {
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
	// Allow empty file? Should be invalid? Treat empty as empty list
	if len(strings.TrimSpace(string(data))) == 0 {
		return []Zone{}, nil
	}
	var zones []Zone
	if err := json.Unmarshal(data, &zones); err != nil {
		return nil, fmt.Errorf("invalid zones json: %w", err)
	}
	for _, z := range zones {
		if strings.TrimSpace(z.ID) == "" {
			return nil, fmt.Errorf("zone id empty")
		}
		hasPoly := len(z.Polygon) > 0
		hasCircle := z.Center != nil || z.RadiusM != nil
		if hasPoly && hasCircle {
			return nil, fmt.Errorf("zone %s has both polygon and circle", z.ID)
		}
		if !hasPoly && !hasCircle {
			return nil, fmt.Errorf("zone %s has neither polygon nor circle", z.ID)
		}
		if hasPoly {
			if len(z.Polygon) < 3 {
				return nil, fmt.Errorf("zone %s polygon <3", z.ID)
			}
			for _, pt := range z.Polygon {
				if !validatePoint(pt) {
					return nil, fmt.Errorf("zone %s invalid point", z.ID)
				}
			}
			for hi, hole := range z.Holes {
				if len(hole) < 3 {
					return nil, fmt.Errorf("zone %s hole %d <3", z.ID, hi)
				}
				for _, pt := range hole {
					if !validatePoint(pt) {
						return nil, fmt.Errorf("zone %s hole %d invalid", z.ID, hi)
					}
				}
			}
		}
		if hasCircle {
			if z.Center == nil || z.RadiusM == nil {
				return nil, fmt.Errorf("zone %s circle missing center or radius", z.ID)
			}
			if !validatePoint(*z.Center) {
				return nil, fmt.Errorf("zone %s center invalid", z.ID)
			}
			if isInvalidFloat(*z.RadiusM) || *z.RadiusM <= 0 || *z.RadiusM > 1000000 {
				return nil, fmt.Errorf("zone %s radius invalid", z.ID)
			}
		}
		if z.ActiveFrom != nil && z.ActiveTo != nil {
			if *z.ActiveFrom > *z.ActiveTo {
				return nil, fmt.Errorf("zone %s active_from > active_to", z.ID)
			}
			if *z.ActiveFrom < 0 || *z.ActiveTo < 0 {
				return nil, fmt.Errorf("zone %s active negative", z.ID)
			}
		} else {
			if z.ActiveFrom != nil && *z.ActiveFrom < 0 {
				return nil, fmt.Errorf("zone %s active_from negative", z.ID)
			}
			if z.ActiveTo != nil && *z.ActiveTo < 0 {
				return nil, fmt.Errorf("zone %s active_to negative", z.ID)
			}
		}
	}
	return zones, nil
}

func isZoneActiveAt(z Zone, ts int64) bool {
	if z.ActiveFrom != nil && ts < *z.ActiveFrom {
		return false
	}
	if z.ActiveTo != nil && ts > *z.ActiveTo {
		return false
	}
	return true
}

func filterActiveZones(zones []Zone, ts int64, useTimeFilter bool) []Zone {
	if !useTimeFilter {
		return zones
	}
	var out []Zone
	for _, z := range zones {
		if isZoneActiveAt(z, ts) {
			out = append(out, z)
		}
	}
	return out
}

func isInsideZone(lat, lng float64, z Zone) bool {
	if len(z.Polygon) > 0 {
		return pointInPolygonWithHoles(lat, lng, z.Polygon, z.Holes)
	}
	if z.Center != nil && z.RadiusM != nil {
		d := haversine(lat, lng, z.Center.Lat, z.Center.Lng)
		return d <= *z.RadiusM+1e-6
	}
	return false
}

func isInsideAnyZoneList(lat, lng float64, zones []Zone) (bool, string) {
	for _, z := range zones {
		if isInsideZone(lat, lng, z) {
			return true, z.ID
		}
	}
	return false, ""
}

// For update: if filtered active zones empty => allow (return true)
func isInsideAnyZoneForUpdate(lat, lng float64, zones []Zone, ts int64) (bool, string) {
	active := filterActiveZones(zones, ts, true)
	if len(active) == 0 {
		return true, ""
	}
	return isInsideAnyZoneList(lat, lng, active)
}

// For near/list: if filtered active zones empty (when time filter), then no zone active => no vehicle passes
func isInsideAnyZoneForFiltering(lat, lng float64, zones []Zone, ts int64, useTimeFilter bool) (bool, string) {
	active := filterActiveZones(zones, ts, useTimeFilter)
	if len(active) == 0 {
		// If zones file had entries but none active, then filtering should result in no match
		// However if original zones empty, caller already handled
		// To distinguish: if original zones empty, allow? For near/list zones filter, if zones file empty, allow all? Spec says if zones list non-empty, location must be inside. If file empty array, then allow all.
		// If file non-empty but no active at ts, then treat as no vehicle passes (inside false)
		// But need to know if original had zones. We'll handle outside.
		if len(zones) == 0 {
			return true, ""
		}
		// No active zones but zones exist => treat as no match
		return false, ""
	}
	return isInsideAnyZoneList(lat, lng, active)
}

// Roads

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
		hasPoints := len(e.Points) > 0
		hasSeg := e.Start != nil && e.End != nil
		if !hasPoints && !hasSeg {
			return nil, fmt.Errorf("road %s missing points and start/end", e.ID)
		}
		if hasPoints {
			if len(e.Points) < 2 {
				return nil, fmt.Errorf("road %s points <2", e.ID)
			}
			for _, pt := range e.Points {
				if !validatePoint(pt) {
					return nil, fmt.Errorf("road %s invalid point", e.ID)
				}
			}
		}
		if hasSeg {
			if !validatePoint(*e.Start) || !validatePoint(*e.End) {
				return nil, fmt.Errorf("road %s start/end invalid", e.ID)
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
	Snapped   bool
	Lat       float64
	Lng       float64
	RoadID    string
	DistM     float64
	Bearing   float64
}

func snapToRoads(lat, lng float64, roads []RoadEntry) SnapResult {
	if len(roads) == 0 {
		return SnapResult{Snapped: false}
	}
	// Convert query to XY once per latRef? We need per segment latRef = lat (query lat) for simplicity using same ref
	latRef := lat
	px, py := latLngToXY(lat, lng, latRef)
	bestDist := math.MaxFloat64
	var best SnapResult
	best.Snapped = false
	for _, road := range roads {
		var pts []Point
		if len(road.Points) >= 2 {
			pts = road.Points
		} else if road.Start != nil && road.End != nil {
			pts = []Point{*road.Start, *road.End}
		} else {
			continue
		}
		for i := 0; i < len(pts)-1; i++ {
			p1 := pts[i]
			p2 := pts[i+1]
			x1, y1 := latLngToXY(p1.Lat, p1.Lng, latRef)
			x2, y2 := latLngToXY(p2.Lat, p2.Lng, latRef)
			cx, cy, dist, _ := closestPointOnSegment(px, py, x1, y1, x2, y2)
			if dist < bestDist {
				bestDist = dist
				cLat, cLng := xyToLatLng(cx, cy, latRef)
				// Bearing of segment
				br := bearing(p1.Lat, p1.Lng, p2.Lat, p2.Lng)
				best = SnapResult{Snapped: true, Lat: cLat, Lng: cLng, RoadID: road.ID, DistM: dist, Bearing: br}
			}
		}
	}
	if best.Snapped && bestDist <= 50.0+1e-9 {
		return best
	}
	return SnapResult{Snapped: false}
}

func bearing(lat1, lng1, lat2, lng2 float64) float64 {
	phi1 := lat1 * math.Pi / 180
	phi2 := lat2 * math.Pi / 180
	dlambda := (lng2 - lng1) * math.Pi / 180
	x := math.Sin(dlambda) * math.Cos(phi2)
	y := math.Cos(phi1)*math.Sin(phi2) - math.Sin(phi1)*math.Cos(phi2)*math.Cos(dlambda)
	br := math.Atan2(x, y) * 180 / math.Pi
	br = math.Mod(br+360, 360)
	return br
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
	// Check if array (invalid)
	var raw json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("corrupt")
	}
	// Try to unmarshal as map
	var m map[string]Location
	if err := json.Unmarshal(data, &m); err != nil {
		// Could be array or other
		return nil, fmt.Errorf("corrupt")
	}
	// Additional check: if JSON is array, above would fail, but also check if data starts with [
	if len(trimmed) > 0 && trimmed[0] == '[' {
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
	return os.Rename(tmp, path)
}

func parseGlobalDBFlag(args []string) (dbPath string, remaining []string) {
	dbPath = ""
	remaining = []string{}
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

type UpdateArgs struct {
	VehicleID string
	Lat       float64
	Lng       float64
	Timestamp int64
	Accuracy  float64
	Speed     float64
	Heading   float64
	ZonesPath string
	hasAcc    bool
	hasSpeed  bool
	hasHead   bool
}

func parseUpdateArgs(cmdArgs []string) UpdateArgs {
	if len(cmdArgs) < 4 {
		exitPrint(2, "update requires <vehicle_id> <lat> <lng> <timestamp_ms>", true)
	}
	ua := UpdateArgs{}
	ua.VehicleID = cmdArgs[0]
	if !vehicleIDRegex.MatchString(ua.VehicleID) {
		exitPrint(2, "invalid vehicle_id", true)
	}
	f, ok := parseFloatArg(cmdArgs[1], "lat")
	if !ok || f < -90 || f > 90 {
		exitPrint(2, "invalid lat", true)
	}
	ua.Lat = f
	f, ok = parseFloatArg(cmdArgs[2], "lng")
	if !ok || f < -180 || f > 180 {
		exitPrint(2, "invalid lng", true)
	}
	ua.Lng = f
	ts, ok := parseIntArg(cmdArgs[3], "timestamp")
	if !ok || ts < 0 {
		exitPrint(2, "invalid timestamp", true)
	}
	ua.Timestamp = ts
	ua.Accuracy = 10.0
	ua.Speed = 0.0
	ua.Heading = 0.0
	// parse flags
	for i := 4; i < len(cmdArgs); i++ {
		arg := cmdArgs[i]
		if arg == "--accuracy" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --accuracy value", true)
			}
			v, ok := parseFloatArg(cmdArgs[i+1], "accuracy")
			if !ok || v < 0 {
				exitPrint(2, "invalid accuracy", true)
			}
			ua.Accuracy = v
			ua.hasAcc = true
			i++
		} else if strings.HasPrefix(arg, "--accuracy=") {
			v, ok := parseFloatArg(arg[len("--accuracy="):], "accuracy")
			if !ok || v < 0 {
				exitPrint(2, "invalid accuracy", true)
			}
			ua.Accuracy = v
			ua.hasAcc = true
		} else if arg == "--speed" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --speed value", true)
			}
			v, ok := parseFloatArg(cmdArgs[i+1], "speed")
			if !ok || v < 0 || v > 50 {
				exitPrint(2, "invalid speed", true)
			}
			ua.Speed = v
			ua.hasSpeed = true
			i++
		} else if strings.HasPrefix(arg, "--speed=") {
			v, ok := parseFloatArg(arg[len("--speed="):], "speed")
			if !ok || v < 0 || v > 50 {
				exitPrint(2, "invalid speed", true)
			}
			ua.Speed = v
			ua.hasSpeed = true
		} else if arg == "--heading" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --heading value", true)
			}
			v, ok := parseFloatArg(cmdArgs[i+1], "heading")
			if !ok || v < 0 || v >= 360 {
				exitPrint(2, "invalid heading", true)
			}
			ua.Heading = v
			ua.hasHead = true
			i++
		} else if strings.HasPrefix(arg, "--heading=") {
			v, ok := parseFloatArg(arg[len("--heading="):], "heading")
			if !ok || v < 0 || v >= 360 {
				exitPrint(2, "invalid heading", true)
			}
			ua.Heading = v
			ua.hasHead = true
		} else if arg == "--zones" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --zones value", true)
			}
			ua.ZonesPath = cmdArgs[i+1]
			i++
		} else if strings.HasPrefix(arg, "--zones=") {
			ua.ZonesPath = arg[len("--zones="):]
		} else {
			exitPrint(2, fmt.Sprintf("unknown flag %s", arg), true)
		}
	}
	return ua
}

func printHelp() {
	fmt.Println("locationctl help")
	fmt.Println("Commands: update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check")
	fmt.Println("Usage: locationctl --db <PATH> <command> [args] [flags]")
}

func main() {
	args := os.Args[1:]
	// If no command or help appears alone or as first arg
	if len(args) == 0 {
		printHelp()
		return
	}
	// Check if first args contain help as standalone
	// If any arg is help/--help/-h and no other command? Spec: If no command or help / --help / -h appears alone or as first arg, print help.
	// Simplistic: if first non-db arg is help, --help, -h
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
		ua := parseUpdateArgs(cmdArgs)
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		// Zones check
		zonesPath := ua.ZonesPath
		if zonesPath == "" {
			// default
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
		// Check if out of zone (using active filtering)
		if len(zones) > 0 {
			ok, _ := isInsideAnyZoneForUpdate(ua.Lat, ua.Lng, zones, ua.Timestamp)
			if !ok {
				fmt.Println("out_of_zone")
				os.Exit(3)
			}
		}
		// Stale check
		if existing, ok := db[ua.VehicleID]; ok {
			if ua.Timestamp <= existing.TimestampMs {
				fmt.Println("stale")
				os.Exit(0)
			}
			// compute distance
			dist := haversine(existing.Lat, existing.Lng, ua.Lat, ua.Lng)
			// maintain history
			history := existing.History
			// push current existing as history? Actually history contains last accepted locations sorted asc. On update, we should push new entry and keep old history plus existing? Simpler: existing history already contains previous locations including existing current? According spec history includes current as last. So after update, history should be old history plus entry for new location? But spec says history last up to 10 accepted locations sorted asc includes current. So we need to create new history entry from old existing? Actually old existing's history already includes its own previous. We should take existing.History, append entry for existing current? But existing.History already includes current? Let's check spec: history last up to 10 accepted locations sorted asc includes current. So before update, existing.History last element should be existing location. After update, new history should be old history (without oldest if exceeding) plus new entry, trimmed to 10 and sorted. However if old history does not include existing (maybe old DB without history), we need to handle.
			// For simplicity, build new history: take existing.History, ensure sorted, then append new entry, then trim oldest.
			// But also need to include existing current if not already in history? Let's assume history already includes existing current as last, so we just append new.
			newEntry := HistoryEntry{Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp, Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading}
			// Ensure existing history sorted
			sort.Slice(history, func(i, j int) bool { return history[i].TimestampMs < history[j].TimestampMs })
			history = append(history, newEntry)
			// Trim oldest if >10
			if len(history) > 10 {
				history = history[len(history)-10:]
			}
			// Sort again
			sort.Slice(history, func(i, j int) bool { return history[i].TimestampMs < history[j].TimestampMs })
			loc := Location{
				VehicleID: ua.VehicleID, Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp,
				Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading,
				TotalDistanceM: existing.TotalDistanceM + dist,
				History: history,
			}
			db[ua.VehicleID] = loc
			// Save
			if err := saveDB(dbPath, db); err != nil {
				exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
			}
			// Print without history
			out := map[string]interface{}{
				"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs,
				"accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading,
				"total_distance_m": loc.TotalDistanceM,
			}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
			os.Exit(0)
		} else {
			// new vehicle
			hist := []HistoryEntry{{Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp, Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading}}
			loc := Location{
				VehicleID: ua.VehicleID, Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp,
				Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading,
				TotalDistanceM: 0,
				History: hist,
			}
			db[ua.VehicleID] = loc
			if err := saveDB(dbPath, db); err != nil {
				exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
			}
			out := map[string]interface{}{
				"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs,
				"accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading,
				"total_distance_m": loc.TotalDistanceM,
			}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
			os.Exit(0)
		}

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
			out := map[string]interface{}{
				"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs,
				"accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading,
				"total_distance_m": loc.TotalDistanceM,
			}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
		}
		os.Exit(0)

	case "list":
		// flags: --since --until --limit --offset --zones --roads --now
		var since, until *int64
		limit := -1
		offset := 0
		hasLimit := false
		hasOffset := false
		zonesPath := ""
		roadsPath := ""
		var nowTs *int64
		i := 0
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--since" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --since value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "since")
				if !ok || v < 0 {
					exitPrint(2, "invalid since", true)
				}
				since = &v
				i += 2
			} else if strings.HasPrefix(a, "--since=") {
				v, ok := parseIntArg(a[len("--since="):], "since")
				if !ok || v < 0 {
					exitPrint(2, "invalid since", true)
				}
				since = &v
				i++
			} else if a == "--until" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --until value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "until")
				if !ok || v < 0 {
					exitPrint(2, "invalid until", true)
				}
				until = &v
				i += 2
			} else if strings.HasPrefix(a, "--until=") {
				v, ok := parseIntArg(a[len("--until="):], "until")
				if !ok || v < 0 {
					exitPrint(2, "invalid until", true)
				}
				until = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "limit")
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):], "limit")
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
				v, ok := parseIntArg(cmdArgs[i+1], "offset")
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				hasOffset = true
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):], "offset")
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				hasOffset = true
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
			} else if a == "--now" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --now value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "now")
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i += 2
			} else if strings.HasPrefix(a, "--now=") {
				v, ok := parseIntArg(a[len("--now="):], "now")
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i++
			} else {
				exitPrint(2, fmt.Sprintf("unknown flag %s", a), true)
			}
		}
		if since != nil && until != nil && *since > *until {
			exitPrint(2, "since > until", true)
		}
		_ = hasLimit
		_ = hasOffset
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
			// zones filter
			if zonesPath != "" {
				useTime := nowTs != nil
				var ts int64 = 0
				if nowTs != nil {
					ts = *nowTs
				}
				// If filtering and original zones non-empty, need to check
				if len(zones) == 0 {
					// empty zones file -> allow all
				} else {
					if useTime {
						active := filterActiveZones(zones, ts, true)
						if len(active) == 0 {
							continue
						}
						ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, active)
						if !ok {
							continue
						}
					} else {
						ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, zones)
						if !ok {
							continue
						}
					}
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
		// pagination offset then limit
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
			} else if limit == -1 {
				// keep all
			}
		} else {
			// no limit specified, keep all (already)
		}
		// Actually if limit not specified default -1 all; we handled
		if limit >= 0 && hasLimit {
			// already trimmed above, but if limit > len keep
		}
		// Output base objects including total_distance_m sorted already
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
		os.Exit(0)

	case "near":
		// required lat lng radius
		var latSet, lngSet, radiusSet bool
		var lat, lng, radius float64
		var accuracyMax *float64
		var speedMin *float64
		limit := -1
		hasLimit := false
		offset := 0
		// hasOffset not needed
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
				v, ok := parseFloatArg(cmdArgs[i+1], "lat")
				if !ok || v < -90 || v > 90 {
					exitPrint(2, "invalid lat", true)
				}
				lat = v
				latSet = true
				i += 2
			} else if strings.HasPrefix(a, "--lat=") {
				v, ok := parseFloatArg(a[len("--lat="):], "lat")
				if !ok || v < -90 || v > 90 {
					exitPrint(2, "invalid lat", true)
				}
				lat = v
				latSet = true
				i++
			} else if a == "--lng" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --lng", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1], "lng")
				if !ok || v < -180 || v > 180 {
					exitPrint(2, "invalid lng", true)
				}
				lng = v
				lngSet = true
				i += 2
			} else if strings.HasPrefix(a, "--lng=") {
				v, ok := parseFloatArg(a[len("--lng="):], "lng")
				if !ok || v < -180 || v > 180 {
					exitPrint(2, "invalid lng", true)
				}
				lng = v
				lngSet = true
				i++
			} else if a == "--radius" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --radius", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1], "radius")
				if !ok || v < 0 || v > 50000 {
					exitPrint(2, "invalid radius", true)
				}
				radius = v
				radiusSet = true
				i += 2
			} else if strings.HasPrefix(a, "--radius=") {
				v, ok := parseFloatArg(a[len("--radius="):], "radius")
				if !ok || v < 0 || v > 50000 {
					exitPrint(2, "invalid radius", true)
				}
				radius = v
				radiusSet = true
				i++
			} else if a == "--accuracy-max" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --accuracy-max", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1], "accuracy-max")
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy-max", true)
				}
				accuracyMax = &v
				i += 2
			} else if strings.HasPrefix(a, "--accuracy-max=") {
				v, ok := parseFloatArg(a[len("--accuracy-max="):], "accuracy-max")
				if !ok || v < 0 {
					exitPrint(2, "invalid accuracy-max", true)
				}
				accuracyMax = &v
				i++
			} else if a == "--speed-min" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --speed-min", true)
				}
				v, ok := parseFloatArg(cmdArgs[i+1], "speed-min")
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed-min", true)
				}
				speedMin = &v
				i += 2
			} else if strings.HasPrefix(a, "--speed-min=") {
				v, ok := parseFloatArg(a[len("--speed-min="):], "speed-min")
				if !ok || v < 0 || v > 50 {
					exitPrint(2, "invalid speed-min", true)
				}
				speedMin = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "limit")
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):], "limit")
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i++
			} else if a == "--offset" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --offset", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "offset")
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):], "offset")
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i++
			} else if a == "--now" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --now", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "now")
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i += 2
			} else if strings.HasPrefix(a, "--now=") {
				v, ok := parseIntArg(a[len("--now="):], "now")
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
					exitPrint(2, "missing --zones", true)
				}
				zonesPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--zones=") {
				zonesPath = a[len("--zones="):]
				i++
			} else if a == "--roads" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --roads", true)
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
		if !latSet || !lngSet || !radiusSet {
			exitPrint(2, "near requires --lat --lng --radius", true)
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
			Loc      Location
			Dist     float64
		}
		var entries []nearEntry
		for _, loc := range db {
			// stale filtering if now provided
			if nowTs != nil && !includeStale {
				age := *nowTs - loc.TimestampMs
				if age < 0 {
					age = 0
				}
				if age > 30000 {
					continue
				}
			}
			if accuracyMax != nil && loc.Accuracy > *accuracyMax+1e-9 {
				continue
			}
			if speedMin != nil && loc.Speed < *speedMin-1e-9 {
				continue
			}
			if zonesPath != "" {
				useTime := nowTs != nil
				var ts int64 = 0
				if nowTs != nil {
					ts = *nowTs
				}
				if len(zones) == 0 {
					// allow
				} else {
					if useTime {
						active := filterActiveZones(zones, ts, true)
						if len(active) == 0 {
							continue
						}
						ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, active)
						if !ok {
							continue
						}
					} else {
						ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, zones)
						if !ok {
							continue
						}
					}
				}
			}
			if roadsPath != "" {
				snap := snapToRoads(loc.Lat, loc.Lng, roads)
				if !snap.Snapped {
					continue
				}
			}
			d := haversine(lat, lng, loc.Lat, loc.Lng)
			if d > radius+1e-9 {
				continue
			}
			entries = append(entries, nearEntry{loc, d})
		}
		sort.Slice(entries, func(i, j int) bool {
			if math.Abs(entries[i].Dist-entries[j].Dist) > 1e-9 {
				return entries[i].Dist < entries[j].Dist
			}
			return entries[i].Loc.VehicleID < entries[j].Loc.VehicleID
		})
		// pagination
		if offset > len(entries) {
			entries = []nearEntry{}
		} else {
			entries = entries[offset:]
		}
		if hasLimit {
			if limit == 0 {
				entries = []nearEntry{}
			} else if limit >= 0 && limit < len(entries) {
				entries = entries[:limit]
			}
		}
		// output
		var out []map[string]interface{}
		for _, e := range entries {
			m := map[string]interface{}{
				"vehicle_id": e.Loc.VehicleID, "lat": e.Loc.Lat, "lng": e.Loc.Lng, "timestamp_ms": e.Loc.TimestampMs,
				"accuracy": e.Loc.Accuracy, "speed": e.Loc.Speed, "heading": e.Loc.Heading,
				"total_distance_m": e.Loc.TotalDistanceM, "distance_m": e.Dist,
			}
			out = append(out, m)
		}
		if out == nil {
			out = []map[string]interface{}{}
		}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))
		os.Exit(0)

	case "track":
		if len(cmdArgs) < 1 {
			exitPrint(2, "track requires <vehicle_id>", true)
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
				v, ok := parseIntArg(cmdArgs[i+1], "from")
				if !ok || v < 0 {
					exitPrint(2, "invalid from", true)
				}
				from = &v
				i += 2
			} else if strings.HasPrefix(a, "--from=") {
				v, ok := parseIntArg(a[len("--from="):], "from")
				if !ok || v < 0 {
					exitPrint(2, "invalid from", true)
				}
				from = &v
				i++
			} else if a == "--to" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --to", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "to")
				if !ok || v < 0 {
					exitPrint(2, "invalid to", true)
				}
				to = &v
				i += 2
			} else if strings.HasPrefix(a, "--to=") {
				v, ok := parseIntArg(a[len("--to="):], "to")
				if !ok || v < 0 {
					exitPrint(2, "invalid to", true)
				}
				to = &v
				i++
			} else if a == "--limit" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --limit", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "limit")
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i += 2
			} else if strings.HasPrefix(a, "--limit=") {
				v, ok := parseIntArg(a[len("--limit="):], "limit")
				if !ok || v < 0 {
					exitPrint(2, "invalid limit", true)
				}
				limit = int(v)
				hasLimit = true
				i++
			} else if a == "--offset" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --offset", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "offset")
				if !ok || v < 0 {
					exitPrint(2, "invalid offset", true)
				}
				offset = int(v)
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):], "offset")
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
		var filtered []HistoryEntry
		for _, h := range loc.History {
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
			} else if limit >= 0 && limit < len(filtered) {
				filtered = filtered[:limit]
			}
		}
		if filtered == nil {
			filtered = []HistoryEntry{}
		}
		b, _ := json.Marshal(filtered)
		fmt.Println(string(b))
		os.Exit(0)

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
		os.Exit(0)

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
		if _, ok := db[vid]; ok {
			delete(db, vid)
			if err := saveDB(dbPath, db); err != nil {
				exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
			}
			fmt.Println("deleted")
		} else {
			fmt.Println("not_found")
		}
		os.Exit(0)

	case "stats":
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		live := len(db)
		totalUpdates := 0
		totalDist := 0.0
		accSum := 0.0
		for _, loc := range db {
			totalUpdates += len(loc.History)
			totalDist += loc.TotalDistanceM
			accSum += loc.Accuracy
		}
		avg := 0.0
		if live > 0 {
			avg = accSum / float64(live)
		}
		out := map[string]interface{}{"live": live, "total_updates": totalUpdates, "total_distance_m": totalDist, "avg_accuracy": avg}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))
		os.Exit(0)

	case "batch":
		// Read stdin
		scanner := bufio.NewScanner(os.Stdin)
		type batchOp struct {
			Op        string
			VehicleID string
			Lat       float64
			Lng       float64
			Ts        int64
			Accuracy  float64
			Speed     float64
			Heading   float64
		}
		var ops []batchOp
		lineNo := 0
		for scanner.Scan() {
			lineNo++
			line := scanner.Text()
			if strings.TrimSpace(line) == "" {
				continue
			}
			parts := strings.Split(line, "\t")
			if len(parts) == 0 {
				continue
			}
			opType := strings.TrimSpace(parts[0])
			if opType == "update" {
				if len(parts) < 5 {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid update format need at least 5", lineNo), true)
				}
				if len(parts) > 8 {
					exitPrint(2, fmt.Sprintf("batch line %d: too many fields", lineNo), true)
				}
				vid := strings.TrimSpace(parts[1])
				if !vehicleIDRegex.MatchString(vid) {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid vehicle_id", lineNo), true)
				}
				latStr := strings.TrimSpace(parts[2])
				lngStr := strings.TrimSpace(parts[3])
				tsStr := strings.TrimSpace(parts[4])
				lat, ok := parseFloatArg(latStr, "")
				if !ok || lat < -90 || lat > 90 {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid lat", lineNo), true)
				}
				lng, ok := parseFloatArg(lngStr, "")
				if !ok || lng < -180 || lng > 180 {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid lng", lineNo), true)
				}
				ts, ok := parseIntArg(tsStr, "")
				if !ok || ts < 0 {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid ts", lineNo), true)
				}
				acc := 10.0
				spd := 0.0
				hdg := 0.0
				if len(parts) >= 6 {
					s := strings.TrimSpace(parts[5])
					if s != "" {
						v, ok := parseFloatArg(s, "")
						if !ok || v < 0 {
							exitPrint(2, fmt.Sprintf("batch line %d: invalid accuracy", lineNo), true)
						}
						acc = v
					}
				}
				if len(parts) >= 7 {
					s := strings.TrimSpace(parts[6])
					if s != "" {
						v, ok := parseFloatArg(s, "")
						if !ok || v < 0 || v > 50 {
							exitPrint(2, fmt.Sprintf("batch line %d: invalid speed", lineNo), true)
						}
						spd = v
					}
				}
				if len(parts) >= 8 {
					s := strings.TrimSpace(parts[7])
					if s != "" {
						v, ok := parseFloatArg(s, "")
						if !ok || v < 0 || v >= 360 {
							exitPrint(2, fmt.Sprintf("batch line %d: invalid heading", lineNo), true)
						}
						hdg = v
					}
				}
				ops = append(ops, batchOp{Op: "update", VehicleID: vid, Lat: lat, Lng: lng, Ts: ts, Accuracy: acc, Speed: spd, Heading: hdg})
			} else if opType == "delete" {
				if len(parts) != 2 {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid delete format", lineNo), true)
				}
				vid := strings.TrimSpace(parts[1])
				if !vehicleIDRegex.MatchString(vid) {
					exitPrint(2, fmt.Sprintf("batch line %d: invalid vehicle_id", lineNo), true)
				}
				ops = append(ops, batchOp{Op: "delete", VehicleID: vid})
			} else {
				exitPrint(2, fmt.Sprintf("batch line %d: unknown op %s", lineNo, opType), true)
			}
		}
		if err := scanner.Err(); err != nil {
			exitPrint(2, fmt.Sprintf("batch read error: %v", err), true)
		}
		// Load DB
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		// Load default zones if exists
		var zones []Zone
		if _, err := os.Stat("/app/data/zones.json"); err == nil {
			z, err := loadZones("/app/data/zones.json")
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
		// Validate zones out_of_zone for each update op (even stale, per hardening)
		if len(zones) > 0 {
			for _, op := range ops {
				if op.Op == "update" {
					ok, _ := isInsideAnyZoneForUpdate(op.Lat, op.Lng, zones, op.Ts)
					if !ok {
						exitPrint(2, fmt.Sprintf("batch: out_of_zone for vehicle %s", op.VehicleID), true)
					}
				}
			}
		}
		// Apply sequentially
		applied := 0
		// Need copy of db for simulation
		for _, op := range ops {
			if op.Op == "delete" {
				if _, ok := db[op.VehicleID]; ok {
					delete(db, op.VehicleID)
					applied++
				}
				continue
			}
			// update
			if existing, ok := db[op.VehicleID]; ok {
				if op.Ts <= existing.TimestampMs {
					continue // stale skip
				}
				dist := haversine(existing.Lat, existing.Lng, op.Lat, op.Lng)
				hist := existing.History
				newEntry := HistoryEntry{Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts, Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading}
				sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
				hist = append(hist, newEntry)
				if len(hist) > 10 {
					hist = hist[len(hist)-10:]
				}
				sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
				loc := Location{
					VehicleID: op.VehicleID, Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts,
					Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading,
					TotalDistanceM: existing.TotalDistanceM + dist,
					History: hist,
				}
				db[op.VehicleID] = loc
				applied++
			} else {
				hist := []HistoryEntry{{Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts, Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading}}
				loc := Location{
					VehicleID: op.VehicleID, Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts,
					Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading,
					TotalDistanceM: 0, History: hist,
				}
				db[op.VehicleID] = loc
				applied++
			}
		}
		if err := saveDB(dbPath, db); err != nil {
			exitPrint(2, fmt.Sprintf("batch save failed: %v", err), true)
		}
		fmt.Printf("batch_ok %d\n", applied)
		os.Exit(0)

	case "clear":
		db := make(map[string]Location)
		if err := saveDB(dbPath, db); err != nil {
			exitPrint(2, fmt.Sprintf("clear save failed: %v", err), true)
		}
		fmt.Println("cleared")
		os.Exit(0)

	case "geofence-check":
		if len(cmdArgs) < 2 {
			exitPrint(2, "geofence-check requires <lat> <lng>", true)
		}
		lat, ok := parseFloatArg(cmdArgs[0], "lat")
		if !ok || lat < -90 || lat > 90 {
			exitPrint(2, "invalid lat", true)
		}
		lng, ok := parseFloatArg(cmdArgs[1], "lng")
		if !ok || lng < -180 || lng > 180 {
			exitPrint(2, "invalid lng", true)
		}
		zonesPath := ""
		var nowTs *int64
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
			} else if a == "--now" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --now value", true)
				}
				v, ok := parseIntArg(cmdArgs[i+1], "now")
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i += 2
			} else if strings.HasPrefix(a, "--now=") {
				v, ok := parseIntArg(a[len("--now="):], "now")
				if !ok || v < 0 {
					exitPrint(2, "invalid now", true)
				}
				nowTs = &v
				i++
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
			b, _ := json.Marshal(map[string]interface{}{"inside": false, "zone_id": ""})
			fmt.Println(string(b))
			os.Exit(0)
		}
		useTime := nowTs != nil
		var ts int64 = 0
		if nowTs != nil {
			ts = *nowTs
		}
		var active []Zone
		if useTime {
			active = filterActiveZones(zones, ts, true)
			if len(active) == 0 {
				b, _ := json.Marshal(map[string]interface{}{"inside": false, "zone_id": ""})
				fmt.Println(string(b))
				os.Exit(0)
			}
		} else {
			active = zones
		}
		inside, zid := isInsideAnyZoneList(lat, lng, active)
		b, _ := json.Marshal(map[string]interface{}{"inside": inside, "zone_id": zid})
		fmt.Println(string(b))
		os.Exit(0)

	default:
		exitPrint(2, fmt.Sprintf("unknown command %s", cmd), false)
	}
}

GOMAIN
cd /app/src && go build -o locationctl . && echo "1_step_one extreme hardened built"
