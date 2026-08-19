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
	OutlierCount   int            `json:"outlier_count,omitempty"`
}

type Zone struct {
	ID         string    `json:"id"`
	Polygon    []Point   `json:"polygon,omitempty"`
	Holes      [][]Point `json:"holes,omitempty"`
	Center     *Point    `json:"center,omitempty"`
	RadiusM    *float64  `json:"radius_m,omitempty"`
	ActiveFrom *int64    `json:"active_from,omitempty"`
	ActiveTo   *int64    `json:"active_to,omitempty"`
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

func isInvalidFloat(f float64) bool { return math.IsNaN(f) || math.IsInf(f, 0) }

func parseFloatArg(s, name string) (float64, bool) {
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

func parseIntArg(s, name string) (int64, bool) {
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

func angularDiff(a, b float64) float64 {
	d := math.Mod(math.Abs(a-b), 360)
	if d > 180 {
		d = 360 - d
	}
	return d
}

func pointOnSegmentEdge(lat, lng float64, a, b Point, eps float64) bool {
	bLngNorm := normalizeLng(b.Lng, a.Lng)
	pLngNorm := normalizeLng(lng, a.Lng)
	dx := bLngNorm - a.Lng
	dy := b.Lat - a.Lat
	dxp := pLngNorm - a.Lng
	dyp := lat - a.Lat
	cross := dx*dyp - dy*dxp
	if math.Abs(cross) > 1e-9 {
		return false
	}
	if math.Abs(dx) < 1e-12 && math.Abs(dy) < 1e-12 {
		return math.Abs(dxp) < 1e-9 && math.Abs(dyp) < 1e-9
	}
	t := (dxp*dx + dyp*dy) / (dx*dx + dy*dy)
	if t < -1e-9 || t > 1+1e-9 {
		return false
	}
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

func normalizeLng(lng, ref float64) float64 {
	for lng < ref-180 {
		lng += 360
	}
	for lng > ref+180 {
		lng -= 360
	}
	return lng
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
		for curr-prev > 180 {
			curr -= 360
		}
		for curr-prev < -180 {
			curr += 360
		}
		unwrapped[i] = Point{Lat: poly[i].Lat, Lng: curr}
	}
	return unwrapped
}

func unwrapLngForQuery(qLng float64, refLng float64) float64 {
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
	unwrapped := unwrapPolygon(poly)
	qLng := unwrapLngForQuery(lng, unwrapped[0].Lng)
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
	trimmed := strings.TrimSpace(string(data))
	if len(trimmed) == 0 {
		return []Zone{}, nil
	}
	if trimmed[0] != '[' {
		return nil, fmt.Errorf("zones must be array, got: %s", trimmed[:1])
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

func isInsideAnyZoneForUpdate(lat, lng float64, zones []Zone, ts int64) (bool, string) {
	active := filterActiveZones(zones, ts, true)
	if len(active) == 0 {
		return true, ""
	}
	return isInsideAnyZoneList(lat, lng, active)
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
	Snapped bool
	Lat     float64
	Lng     float64
	RoadID  string
	DistM   float64
	Bearing float64
}

func snapToRoads(lat, lng float64, roads []RoadEntry) SnapResult {
	if len(roads) == 0 {
		return SnapResult{Snapped: false}
	}
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

func snapToRoadsHeadingAware(lat, lng, heading float64, speed float64, roads []RoadEntry, headingAware bool) SnapResult {
	if len(roads) == 0 {
		return SnapResult{Snapped: false}
	}
	if !headingAware {
		return snapToRoads(lat, lng, roads)
	}
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
			if dist > 50.0+1e-9 {
				continue
			}
			br := bearing(p1.Lat, p1.Lng, p2.Lat, p2.Lng)
			diff := angularDiff(heading, br)
			diffOpp := angularDiff(heading, math.Mod(br+180, 360))
			minDiff := math.Min(diff, diffOpp)
			if minDiff > 45.0+1e-9 {
				continue // skip candidate
			}
			if dist < bestDist {
				bestDist = dist
				cLat, cLng := xyToLatLng(cx, cy, latRef)
				best = SnapResult{Snapped: true, Lat: cLat, Lng: cLng, RoadID: road.ID, DistM: dist, Bearing: br}
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
				stale := filepath.Join(dir, f.Name())
				_ = os.Remove(stale)
			}
		}
	}
	return nil
}

func parseGlobalDBFlag(args []string) (string, []string) {
	dbPath := ""
	rem := []string{}
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
			rem = append(rem, args[i])
		}
	}
	return dbPath, rem
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
			i++
		} else if strings.HasPrefix(arg, "--accuracy=") {
			v, ok := parseFloatArg(arg[len("--accuracy="):], "accuracy")
			if !ok || v < 0 {
				exitPrint(2, "invalid accuracy", true)
			}
			ua.Accuracy = v
		} else if arg == "--speed" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --speed value", true)
			}
			v, ok := parseFloatArg(cmdArgs[i+1], "speed")
			if !ok || v < 0 || v > 50 {
				exitPrint(2, "invalid speed", true)
			}
			ua.Speed = v
			i++
		} else if strings.HasPrefix(arg, "--speed=") {
			v, ok := parseFloatArg(arg[len("--speed="):], "speed")
			if !ok || v < 0 || v > 50 {
				exitPrint(2, "invalid speed", true)
			}
			ua.Speed = v
		} else if arg == "--heading" {
			if i+1 >= len(cmdArgs) {
				exitPrint(2, "missing --heading value", true)
			}
			v, ok := parseFloatArg(cmdArgs[i+1], "heading")
			if !ok || v < 0 || v >= 360 {
				exitPrint(2, "invalid heading", true)
			}
			ua.Heading = v
			i++
		} else if strings.HasPrefix(arg, "--heading=") {
			v, ok := parseFloatArg(arg[len("--heading="):], "heading")
			if !ok || v < 0 || v >= 360 {
				exitPrint(2, "invalid heading", true)
			}
			ua.Heading = v
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
	fmt.Println("Commands: update,get,list,near,track,distance,delete,stats,batch,clear,estimate,validate-pickup,validate-dropoff,geofence-check")
}

func buildEstimateResult(loc Location, nowMs int64, roads []RoadEntry, roadsPath string) map[string]interface{} {
	age := nowMs - loc.TimestampMs
	if age < 0 {
		age = 0
	}
	hist := loc.History
	sort.Slice(hist, func(i, j int) bool { return hist[i].TimestampMs < hist[j].TimestampMs })
	var recent []HistoryEntry
	if len(hist) >= 5 {
		recent = hist[len(hist)-5:]
	} else {
		recent = hist
	}
	smoothedLat := loc.Lat
	smoothedLng := loc.Lng
	if len(recent) >= 2 {
		var sumW, sumLat, sumLng float64
		for _, h := range recent {
			ageH := nowMs - h.TimestampMs
			if ageH < 0 {
				ageH = 0
			}
			w := (1.0 / (h.Accuracy + 1.0)) * math.Exp(-float64(ageH)/10000.0)
			sumW += w
			sumLat += w * h.Lat
			sumLng += w * h.Lng
		}
		if sumW > 1e-12 {
			smoothedLat = sumLat / sumW
			smoothedLng = sumLng / sumW
		}
	}
	originalSmoothedLat := smoothedLat
	originalSmoothedLng := smoothedLng
	predicted := false
	estLat := smoothedLat
	estLng := smoothedLng
	accuracy := loc.Accuracy
	// Always degrade accuracy with age (0.5 m per sec)
	dtSecTotal := float64(age) / 1000.0
	accuracy = accuracy + 0.5*dtSecTotal
	if age > 0 && age <= 30000 && loc.Speed > 0 {
		predicted = true
		dist := loc.Speed * dtSecTotal
		R := 6371000.0
		headingRad := loc.Heading * math.Pi / 180
		latRad := smoothedLat * math.Pi / 180
		deltaLat := dist * math.Cos(headingRad) / R * 180 / math.Pi
		cosLat := math.Cos(latRad)
		if math.Abs(cosLat) < 1e-12 {
			cosLat = 1e-12
		}
		deltaLng := dist * math.Sin(headingRad) / (R * cosLat) * 180 / math.Pi
		estLat = smoothedLat + deltaLat
		estLng = smoothedLng + deltaLng
	}
	// Clarified: original is always smoothed base BEFORE prediction when not snapped
	// When snapped, original becomes position BEFORE snapping (predicted if predicted)
	originalLat := originalSmoothedLat
	originalLng := originalSmoothedLng
	snapped := false
	roadID := ""
	roadBearing := 0.0
	roadDist := 0.0
	finalLat := estLat
	finalLng := estLng
	if roadsPath != "" {
		headingAware := loc.Speed > 1
		snap := snapToRoadsHeadingAware(estLat, estLng, loc.Heading, loc.Speed, roads, headingAware)
		if snap.Snapped {
			snapped = true
			originalLat = estLat
			originalLng = estLng
			finalLat = snap.Lat
			finalLng = snap.Lng
			roadID = snap.RoadID
			roadBearing = snap.Bearing
			roadDist = snap.DistM
		}
	}
	conf := "low"
	if accuracy <= 5 && age <= 5000 {
		conf = "high"
	} else if accuracy <= 10 && age <= 10000 {
		conf = "high"
	} else if accuracy <= 25 && age <= 20000 {
		conf = "medium"
	}
	if snapped {
		if roadDist <= 10 {
			if conf == "medium" && accuracy <= 25 {
				conf = "high"
			} else if conf == "low" && accuracy <= 40 && age <= 15000 {
				conf = "medium"
			}
		}
	}
	if loc.OutlierCount > 2 && conf == "high" {
		conf = "medium"
	}
	if loc.OutlierCount > 5 {
		conf = "low"
	}
	if age > 30000 {
		conf = "low"
	}
	if accuracy > 50 {
		conf = "low"
	}
	if !snapped && accuracy > 25 && age > 10000 {
		if accuracy > 40 || age > 15000 {
			conf = "low"
		}
	}

	result := map[string]interface{}{
		"vehicle_id": loc.VehicleID,
		"lat": finalLat,
		"lng": finalLng,
		"timestamp_ms": loc.TimestampMs,
		"accuracy": accuracy,
		"speed": loc.Speed,
		"heading": loc.Heading,
		"total_distance_m": loc.TotalDistanceM,
		"confidence": conf,
		"age_ms": age,
		"snapped": snapped,
		"road_id": roadID,
		"road_bearing": roadBearing,
		"road_dist_m": roadDist,
		"predicted": predicted,
		"original_lat": originalLat,
		"original_lng": originalLng,
	}
	return result
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
	if first == "help" || first == "--help" || first == "-h" ||
		strings.HasPrefix(first, "help=") || strings.HasPrefix(first, "--help=") || strings.HasPrefix(first, "-h=") {
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
		// low accuracy filter
		if ua.Accuracy > 100 {
			fmt.Println("low_accuracy")
			os.Exit(3)
		}
		zonesPath := ua.ZonesPath
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
			ok, _ := isInsideAnyZoneForUpdate(ua.Lat, ua.Lng, zones, ua.Timestamp)
			if !ok {
				fmt.Println("out_of_zone")
				os.Exit(3)
			}
		}
		if existing, ok := db[ua.VehicleID]; ok {
			if ua.Timestamp <= existing.TimestampMs {
				fmt.Println("stale")
				os.Exit(0)
			}
			// outlier detection - extreme but not too aggressive for normal moves
			dtSec := float64(ua.Timestamp-existing.TimestampMs) / 1000.0
			if dtSec > 0 {
				dist := haversine(existing.Lat, existing.Lng, ua.Lat, ua.Lng)
				implied := dist / dtSec
				isOutlier := false
				// 1 teleport: large jump short time good accuracy
				if dtSec < 300 && dist > 1000 && implied > 50 && existing.Accuracy < 50 && ua.Accuracy < 50 {
					isOutlier = true
				}
				// 2 heading flip
				if !isOutlier && existing.Speed > 10 && ua.Speed > 10 && angularDiff(existing.Heading, ua.Heading) > 120 && dist < 500 {
					isOutlier = true
				}
				// 3 median deviation: only when history has >=2 and implied is huge outlier vs median
				if !isOutlier && len(existing.History) >= 2 {
					var speeds []float64
					h := existing.History
					sort.Slice(h, func(i, j int) bool { return h[i].TimestampMs < h[j].TimestampMs })
					n := len(h)
					for i := n - 2; i >= 0 && len(speeds) < 3; i-- {
						if h[i+1].TimestampMs > h[i].TimestampMs {
							d := haversine(h[i].Lat, h[i].Lng, h[i+1].Lat, h[i+1].Lng)
							dt := float64(h[i+1].TimestampMs-h[i].TimestampMs) / 1000.0
							if dt > 0 {
								speeds = append(speeds, d/dt)
							}
						}
					}
					if len(speeds) > 0 {
						sorted := append([]float64{}, speeds...)
						sort.Float64s(sorted)
						var med float64
						mid := len(sorted) / 2
						if len(sorted)%2 == 0 {
							med = (sorted[mid-1] + sorted[mid]) / 2
						} else {
							med = sorted[mid]
						}
						if math.Abs(implied-med) > 30 && implied > 30 && dist > 500 {
							isOutlier = true
						}
					}
				}
				// 4 acceleration spike: only if speed change huge and dist small
				if !isOutlier {
					acc := math.Abs(ua.Speed-existing.Speed) / dtSec
					if acc > 15 && dist < 300 {
						isOutlier = true
					}
				}
				// 5 accuracy spike: sudden degradation
				if !isOutlier {
					if ua.Accuracy > 75 && ua.Accuracy > existing.Accuracy*2+30 {
						isOutlier = true
					}
				}
				// 6 speed vs implied: only for very large implied vs stopped
				if !isOutlier {
					if implied > 80 && ua.Speed < 2 && dist > 1000 && dtSec < 60 {
						isOutlier = true
					}
				}
				if isOutlier {
					existing.OutlierCount++
					db[ua.VehicleID] = existing
					saveDB(dbPath, db)
					fmt.Println("outlier")
					os.Exit(3)
				}
			}
			// not outlier, proceed
			dist := haversine(existing.Lat, existing.Lng, ua.Lat, ua.Lng)
			history := existing.History
			newEntry := HistoryEntry{Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp, Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading}
			sort.Slice(history, func(i, j int) bool { return history[i].TimestampMs < history[j].TimestampMs })
			history = append(history, newEntry)
			if len(history) > 10 {
				history = history[len(history)-10:]
			}
			sort.Slice(history, func(i, j int) bool { return history[i].TimestampMs < history[j].TimestampMs })
			loc := Location{
				VehicleID: ua.VehicleID, Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp,
				Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading,
				TotalDistanceM: existing.TotalDistanceM + dist,
				History: history, OutlierCount: existing.OutlierCount,
			}
			db[ua.VehicleID] = loc
			if err := saveDB(dbPath, db); err != nil {
				exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
			}
			out := map[string]interface{}{
				"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs,
				"accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading,
				"total_distance_m": loc.TotalDistanceM, "outlier_count": loc.OutlierCount,
			}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
			os.Exit(0)
		} else {
			hist := []HistoryEntry{{Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp, Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading}}
			loc := Location{
				VehicleID: ua.VehicleID, Lat: ua.Lat, Lng: ua.Lng, TimestampMs: ua.Timestamp,
				Accuracy: ua.Accuracy, Speed: ua.Speed, Heading: ua.Heading,
				TotalDistanceM: 0, History: hist, OutlierCount: 0,
			}
			db[ua.VehicleID] = loc
			if err := saveDB(dbPath, db); err != nil {
				exitPrint(2, fmt.Sprintf("save failed: %v", err), true)
			}
			out := map[string]interface{}{
				"vehicle_id": loc.VehicleID, "lat": loc.Lat, "lng": loc.Lng, "timestamp_ms": loc.TimestampMs,
				"accuracy": loc.Accuracy, "speed": loc.Speed, "heading": loc.Heading,
				"total_distance_m": loc.TotalDistanceM, "outlier_count": loc.OutlierCount,
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
				"total_distance_m": loc.TotalDistanceM, "outlier_count": loc.OutlierCount,
			}
			b, _ := json.Marshal(out)
			fmt.Println(string(b))
		}
		os.Exit(0)

	case "list":
		var since, until *int64
		limit := -1
		hasLimit := false
		offset := 0
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
				i += 2
			} else if strings.HasPrefix(a, "--offset=") {
				v, ok := parseIntArg(a[len("--offset="):], "offset")
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
			if zonesPath != "" {
				useTime := nowTs != nil
				var ts int64 = 0
				if nowTs != nil {
					ts = *nowTs
				}
				if len(zones) == 0 {
				} else {
					if useTime {
						active := filterActiveZones(zones, ts, true)
						if len(active) != 0 {
							ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, active)
							if !ok {
								continue
							}
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
		if offset > len(list) {
			list = []Location{}
		} else {
			list = list[offset:]
		}
		if hasLimit {
			if limit == 0 {
				list = []Location{}
			} else if limit >= 0 && limit < len(list) {
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
			OutlierCount   int     `json:"outlier_count,omitempty"`
		}
		var out []outLoc
		for _, l := range list {
			out = append(out, outLoc{l.VehicleID, l.Lat, l.Lng, l.TimestampMs, l.Accuracy, l.Speed, l.Heading, l.TotalDistanceM, l.OutlierCount})
		}
		if out == nil {
			out = []outLoc{}
		}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))
		os.Exit(0)

	case "near":
		var latSet, lngSet, radiusSet bool
		var lat, lng, radius float64
		var accuracyMax *float64
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
			Loc  Location
			Dist float64
		}
		var entries []nearEntry
		for _, loc := range db {
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
				} else {
					if useTime {
						active := filterActiveZones(zones, ts, true)
						if len(active) != 0 {
							ok, _ := isInsideAnyZoneList(loc.Lat, loc.Lng, active)
							if !ok {
								continue
							}
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
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		var zones []Zone
		if _, err := os.Stat("/app/data/zones.json"); err == nil {
			z, err := loadZones("/app/data/zones.json")
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			zones = z
		}
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
		applied := 0
		for _, op := range ops {
			if op.Op == "delete" {
				if _, ok := db[op.VehicleID]; ok {
					delete(db, op.VehicleID)
					applied++
				}
				continue
			}
			// low_accuracy filter in batch - skip without incrementing outlier_count
			if op.Accuracy > 100 {
				continue
			}
			if existing, ok := db[op.VehicleID]; ok {
				if op.Ts <= existing.TimestampMs {
					continue
				}
				// outlier detection for batch
				dtSec := float64(op.Ts-existing.TimestampMs) / 1000.0
				isOutlier := false
				if dtSec > 0 {
					distChk := haversine(existing.Lat, existing.Lng, op.Lat, op.Lng)
					impliedChk := distChk / dtSec
					if dtSec < 300 && distChk > 1000 && impliedChk > 50 && existing.Accuracy < 50 && op.Accuracy < 50 {
						isOutlier = true
					}
					if !isOutlier && existing.Speed > 10 && op.Speed > 10 && angularDiff(existing.Heading, op.Heading) > 120 && distChk < 500 {
						isOutlier = true
					}
					if !isOutlier && len(existing.History) >= 2 {
						var speeds []float64
						h := existing.History
						sort.Slice(h, func(i, j int) bool { return h[i].TimestampMs < h[j].TimestampMs })
						n := len(h)
						for i := n - 2; i >= 0 && len(speeds) < 3; i-- {
							if h[i+1].TimestampMs > h[i].TimestampMs {
								d := haversine(h[i].Lat, h[i].Lng, h[i+1].Lat, h[i+1].Lng)
								dt := float64(h[i+1].TimestampMs-h[i].TimestampMs) / 1000.0
								if dt > 0 {
									speeds = append(speeds, d/dt)
								}
							}
						}
						if len(speeds) > 0 {
							sorted := append([]float64{}, speeds...)
							sort.Float64s(sorted)
							var med float64
							mid := len(sorted) / 2
							if len(sorted)%2 == 0 {
								med = (sorted[mid-1] + sorted[mid]) / 2
							} else {
								med = sorted[mid]
							}
							if math.Abs(impliedChk-med) > 30 && impliedChk > 30 && distChk > 500 {
								isOutlier = true
							}
						}
					}
					if !isOutlier {
						acc := math.Abs(op.Speed-existing.Speed) / dtSec
						if acc > 15 && distChk < 300 {
							isOutlier = true
						}
					}
					if !isOutlier {
						if op.Accuracy > 75 && op.Accuracy > existing.Accuracy*2+30 {
							isOutlier = true
						}
					}
					if !isOutlier {
						if impliedChk > 80 && op.Speed < 2 && distChk > 1000 && dtSec < 60 {
							isOutlier = true
						}
					}
				}
				if isOutlier {
					existing.OutlierCount++
					db[op.VehicleID] = existing
					continue
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
					History: hist, OutlierCount: existing.OutlierCount,
				}
				db[op.VehicleID] = loc
				applied++
			} else {
				hist := []HistoryEntry{{Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts, Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading}}
				loc := Location{
					VehicleID: op.VehicleID, Lat: op.Lat, Lng: op.Lng, TimestampMs: op.Ts,
					Accuracy: op.Accuracy, Speed: op.Speed, Heading: op.Heading,
					TotalDistanceM: 0, History: hist, OutlierCount: 0,
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

	case "estimate":
		if len(cmdArgs) < 1 {
			exitPrint(2, "estimate requires <vehicle_id>", true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		var nowTs *int64
		roadsPath := ""
		i := 1
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--now" {
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
		db, err := loadDB(dbPath)
		if err != nil {
			exitPrint(4, fmt.Sprintf("corrupt db: %v", err), true)
		}
		loc, ok := db[vid]
		if !ok {
			exitPrint(3, "not found", true)
		}
		nowMs := time.Now().UnixMilli()
		if nowTs != nil {
			nowMs = *nowTs
		}
		var roads []RoadEntry
		if roadsPath != "" {
			r, err := loadRoads(roadsPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid roads file: %v", err), true)
			}
			roads = r
		}
		result := buildEstimateResult(loc, nowMs, roads, roadsPath)
		b, _ := json.Marshal(result)
		fmt.Println(string(b))
		os.Exit(0)

	case "validate-pickup", "validate-dropoff":
		isDropoff := cmd == "validate-dropoff"
		if len(cmdArgs) < 3 {
			exitPrint(2, fmt.Sprintf("%s requires <vehicle_id> <lat> <lng>", cmd), true)
		}
		vid := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vid) {
			exitPrint(2, "invalid vehicle_id", true)
		}
		pLat, ok := parseFloatArg(cmdArgs[1], "lat")
		if !ok || pLat < -90 || pLat > 90 {
			exitPrint(2, "invalid lat", true)
		}
		pLng, ok := parseFloatArg(cmdArgs[2], "lng")
		if !ok || pLng < -180 || pLng > 180 {
			exitPrint(2, "invalid lng", true)
		}
		var nowTs *int64
		roadsPath := ""
		zonesPath := ""
		i := 3
		for i < len(cmdArgs) {
			a := cmdArgs[i]
			if a == "--now" {
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
			} else if a == "--roads" {
				if i+1 >= len(cmdArgs) {
					exitPrint(2, "missing --roads", true)
				}
				roadsPath = cmdArgs[i+1]
				i += 2
			} else if strings.HasPrefix(a, "--roads=") {
				roadsPath = a[len("--roads="):]
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
		nowMs := time.Now().UnixMilli()
		if nowTs != nil {
			nowMs = *nowTs
		}
		var roads []RoadEntry
		if roadsPath != "" {
			r, err := loadRoads(roadsPath)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid roads file: %v", err), true)
			}
			roads = r
		}
		// estimate vehicle
		estResult := buildEstimateResult(loc, nowMs, roads, roadsPath)
		vehLat := estResult["lat"].(float64)
		vehLng := estResult["lng"].(float64)
		accuracy := estResult["accuracy"].(float64)
		speed := estResult["speed"].(float64)
		age := estResult["age_ms"].(int64)
		conf := estResult["confidence"].(string)
		snapped := estResult["snapped"].(bool)
		roadID := estResult["road_id"].(string)

		// snap pickup point
		pickupRoadID := ""
		pickupSnapped := false
		if roadsPath != "" {
			snapPickup := snapToRoads(pLat, pLng, roads)
			if snapPickup.Snapped {
				pickupSnapped = true
				pickupRoadID = snapPickup.RoadID
			}
		}

		// zones for pickup/dropoff
		var pickupZones []Zone
		var pickupZonesPathUsed string
		if zonesPath != "" {
			pickupZonesPathUsed = zonesPath
		} else {
			if isDropoff {
				if _, err := os.Stat("/app/data/dropoff_zones.json"); err == nil {
					pickupZonesPathUsed = "/app/data/dropoff_zones.json"
				}
			} else {
				if _, err := os.Stat("/app/data/pickup_zones.json"); err == nil {
					pickupZonesPathUsed = "/app/data/pickup_zones.json"
				}
			}
		}
		if pickupZonesPathUsed != "" {
			z, err := loadZones(pickupZonesPathUsed)
			if err != nil {
				exitPrint(2, fmt.Sprintf("invalid zones file: %v", err), true)
			}
			pickupZones = z
		}

		// validation order
		valid := true
		reason := "ok"
		exitCode := 0

		// 1 out_of_geofence
		if len(pickupZones) > 0 {
			useTime := nowTs != nil
			var active []Zone
			if useTime {
				active = filterActiveZones(pickupZones, nowMs, true)
			} else {
				active = pickupZones
			}
			if len(active) > 0 {
				ok, _ := isInsideAnyZoneList(pLat, pLng, active)
				if !ok {
					valid = false
					reason = "out_of_geofence"
					exitCode = 1
				}
			}
		}
		// 2 stale
		if valid {
			if age > 30000 {
				valid = false
				reason = "stale"
				exitCode = 1
			}
		}
		// 3 low_accuracy
		if valid {
			if accuracy > 50 {
				valid = false
				reason = "low_accuracy"
				exitCode = 1
			}
		}
		// 4 off_road
		if valid && roadsPath != "" {
			if !snapped {
				valid = false
				reason = "off_road"
				exitCode = 1
			}
		}
		// 5 moving
		if valid {
			threshold := 5.0
			if isDropoff {
				threshold = 10.0
			}
			if speed >= threshold {
				valid = false
				reason = "moving"
				exitCode = 1
			}
		}
		// 6 road_mismatch
		if valid && roadsPath != "" {
			if snapped && pickupSnapped && roadID != "" && pickupRoadID != "" && roadID != pickupRoadID {
				valid = false
				reason = "road_mismatch"
				exitCode = 1
			}
		}
		// 7 heading_mismatch
		if valid && roadsPath != "" {
			if snapped && pickupSnapped && roadID == pickupRoadID && roadID != "" {
				// compute bearing from vehicle to pickup
				if loc.Speed > 1 {
					brToPickup := bearing(vehLat, vehLng, pLat, pLng)
					diff := angularDiff(loc.Heading, brToPickup)
					distToPickup := haversine(vehLat, vehLng, pLat, pLng)
					if diff > 90 && distToPickup > 10 {
						valid = false
						reason = "heading_mismatch"
						exitCode = 1
					}
				}
			}
		}
		// 8 too_far
		if valid {
			dist := haversine(vehLat, vehLng, pLat, pLng)
			thresh := 100.0
			if isDropoff {
				thresh = 150.0
			}
			if dist > thresh {
				valid = false
				reason = "too_far"
				exitCode = 1
			}
		}

		distM := haversine(vehLat, vehLng, pLat, pLng)
		out := map[string]interface{}{
			"valid": valid, "reason": reason, "distance_m": distM,
			"confidence": conf, "age_ms": age, "accuracy": accuracy,
			"snapped": snapped, "road_id": roadID, "pickup_road_id": pickupRoadID,
			"vehicle_lat": vehLat, "vehicle_lng": vehLng,
		}
		if isDropoff {
			// also support dropoff_road_id as alias? Keep pickup_road_id for compat
			out["dropoff_road_id"] = pickupRoadID
		}
		b, _ := json.Marshal(out)
		fmt.Println(string(b))
		os.Exit(exitCode)

	default:
		exitPrint(2, fmt.Sprintf("unknown command %s", cmd), false)
	}
}

GOMAIN
cd /app/src && go build -o locationctl . && echo "Step2 extreme hardened built"
