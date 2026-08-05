#!/bin/bash
set -e
mkdir -p /app/src
cat > /app/src/go.mod <<'GOMOD'
module locationservice

go 1.22
GOMOD

cat > /app/src/main.go <<'GO'
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
)

type Location struct {
	VehicleID   string  `json:"vehicle_id"`
	Lat         float64 `json:"lat"`
	Lng         float64 `json:"lng"`
	TimestampMs int64   `json:"timestamp_ms"`
	Accuracy    float64 `json:"accuracy"`
	Speed       float64 `json:"speed"`
	Heading     float64 `json:"heading"`
}

var vehicleIDRegex = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func exitCode(code int, msg string, isErr bool) {
	if msg != "" {
		if isErr {
			fmt.Fprintln(os.Stderr, msg)
		} else {
			fmt.Fprintln(os.Stdout, msg)
		}
	}
	os.Exit(code)
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
	if len(data) == 0 {
		return db, nil
	}
	if err := json.Unmarshal(data, &db); err != nil {
		return nil, fmt.Errorf("corrupt")
	}
	return db, nil
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

func main() {
	args := os.Args[1:]
	dbPath := ""
	filtered := []string{}
	i := 0
	for i < len(args) {
		if args[i] == "--db" {
			if i+1 >= len(args) {
				exitCode(2, "missing --db value", true)
			}
			dbPath = args[i+1]
			i += 2
		} else if len(args[i]) > 5 && args[i][:5] == "--db=" {
			dbPath = args[i][5:]
			i++
		} else {
			filtered = append(filtered, args[i])
			i++
		}
	}
	if dbPath == "" {
		dbPath = "locations.json"
	}
	if len(filtered) == 0 {
		exitCode(2, "missing command", true)
	}
	cmd := filtered[0]
	cmdArgs := filtered[1:]

	switch cmd {
	case "update":
		if len(cmdArgs) < 4 {
			exitCode(2, "update requires <vehicle_id> <lat> <lng> <timestamp_ms>", true)
		}
		vehicleID := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vehicleID) {
			exitCode(2, "invalid vehicle_id", true)
		}
		lat, err := strconv.ParseFloat(cmdArgs[1], 64)
		if err != nil || math.IsNaN(lat) || math.IsInf(lat, 0) || lat < -90 || lat > 90 {
			exitCode(2, "invalid lat", true)
		}
		lng, err := strconv.ParseFloat(cmdArgs[2], 64)
		if err != nil || math.IsNaN(lng) || math.IsInf(lng, 0) || lng < -180 || lng > 180 {
			exitCode(2, "invalid lng", true)
		}
		ts, err := strconv.ParseInt(cmdArgs[3], 10, 64)
		if err != nil || ts < 0 {
			exitCode(2, "invalid timestamp", true)
		}
		accuracy := 10.0
		speed := 0.0
		heading := 0.0
		j := 4
		for j < len(cmdArgs) {
			switch cmdArgs[j] {
			case "--accuracy":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing accuracy value", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
					exitCode(2, "invalid accuracy", true)
				}
				accuracy = v
				j += 2
			case "--speed":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing speed value", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
					exitCode(2, "invalid speed", true)
				}
				speed = v
				j += 2
			case "--heading":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing heading value", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v >= 360 {
					exitCode(2, "invalid heading", true)
				}
				heading = v
				j += 2
			default:
				if len(cmdArgs[j]) > 11 && cmdArgs[j][:11] == "--accuracy=" {
					vStr := cmdArgs[j][11:]
					v, err := strconv.ParseFloat(vStr, 64)
					if err != nil || v < 0 {
						exitCode(2, "invalid accuracy", true)
					}
					accuracy = v
					j++
				} else if len(cmdArgs[j]) > 8 && cmdArgs[j][:8] == "--speed=" {
					vStr := cmdArgs[j][8:]
					v, err := strconv.ParseFloat(vStr, 64)
					if err != nil || v < 0 {
						exitCode(2, "invalid speed", true)
					}
					speed = v
					j++
				} else if len(cmdArgs[j]) > 10 && cmdArgs[j][:10] == "--heading=" {
					vStr := cmdArgs[j][10:]
					v, err := strconv.ParseFloat(vStr, 64)
					if err != nil || v < 0 || v >= 360 {
						exitCode(2, "invalid heading", true)
					}
					heading = v
					j++
				} else {
					exitCode(2, fmt.Sprintf("unknown flag %s", cmdArgs[j]), true)
				}
			}
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitCode(4, "corrupt database", true)
		}
		if existing, ok := db[vehicleID]; ok {
			if ts <= existing.TimestampMs {
				fmt.Println("stale")
				os.Exit(0)
			}
		}
		loc := Location{
			VehicleID:   vehicleID,
			Lat:         lat,
			Lng:         lng,
			TimestampMs: ts,
			Accuracy:    accuracy,
			Speed:       speed,
			Heading:     heading,
		}
		db[vehicleID] = loc
		if err := saveDB(dbPath, db); err != nil {
			exitCode(2, fmt.Sprintf("failed to save: %v", err), true)
		}
		b, _ := json.Marshal(loc)
		fmt.Println(string(b))

	case "get":
		if len(cmdArgs) < 1 {
			exitCode(2, "get requires vehicle_id", true)
		}
		vehicleID := cmdArgs[0]
		if !vehicleIDRegex.MatchString(vehicleID) {
			exitCode(2, "invalid vehicle_id", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitCode(4, "corrupt database", true)
		}
		loc, ok := db[vehicleID]
		if !ok {
			exitCode(3, "not found", true)
		}
		b, _ := json.Marshal(loc)
		fmt.Println(string(b))

	case "list":
		db, err := loadDB(dbPath)
		if err != nil {
			exitCode(4, "corrupt database", true)
		}
		list := make([]Location, 0, len(db))
		for _, v := range db {
			list = append(list, v)
		}
		sort.Slice(list, func(i, j int) bool {
			return list[i].VehicleID < list[j].VehicleID
		})
		b, _ := json.Marshal(list)
		fmt.Println(string(b))

	case "near":
		lat := math.NaN()
		lng := math.NaN()
		radius := -1.0
		latSet, lngSet, radiusSet := false, false, false
		j := 0
		for j < len(cmdArgs) {
			switch cmdArgs[j] {
			case "--lat":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing lat", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil {
					exitCode(2, "invalid lat", true)
				}
				lat = v
				latSet = true
				j += 2
			case "--lng":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing lng", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil {
					exitCode(2, "invalid lng", true)
				}
				lng = v
				lngSet = true
				j += 2
			case "--radius":
				if j+1 >= len(cmdArgs) {
					exitCode(2, "missing radius", true)
				}
				v, err := strconv.ParseFloat(cmdArgs[j+1], 64)
				if err != nil {
					exitCode(2, "invalid radius", true)
				}
				radius = v
				radiusSet = true
				j += 2
			default:
				if len(cmdArgs[j]) > 6 && cmdArgs[j][:6] == "--lat=" {
					v, _ := strconv.ParseFloat(cmdArgs[j][6:], 64)
					lat = v
					latSet = true
					j++
				} else if len(cmdArgs[j]) > 6 && cmdArgs[j][:6] == "--lng=" {
					v, _ := strconv.ParseFloat(cmdArgs[j][6:], 64)
					lng = v
					lngSet = true
					j++
				} else if len(cmdArgs[j]) > 9 && cmdArgs[j][:9] == "--radius=" {
					v, _ := strconv.ParseFloat(cmdArgs[j][9:], 64)
					radius = v
					radiusSet = true
					j++
				} else if cmdArgs[j] == "--now" {
					if j+1 < len(cmdArgs) {
						j += 2
					} else {
						j++
					}
				} else if len(cmdArgs[j]) > 6 && cmdArgs[j][:6] == "--now=" {
					j++
				} else if cmdArgs[j] == "--include-stale" {
					j++
				} else {
					exitCode(2, fmt.Sprintf("unknown flag %s", cmdArgs[j]), true)
				}
			}
		}
		if !latSet || !lngSet || !radiusSet {
			exitCode(2, "near requires --lat --lng --radius", true)
		}
		if lat < -90 || lat > 90 || math.IsNaN(lat) {
			exitCode(2, "invalid lat", true)
		}
		if lng < -180 || lng > 180 || math.IsNaN(lng) {
			exitCode(2, "invalid lng", true)
		}
		if radius < 0 || radius > 50000 {
			exitCode(2, "invalid radius", true)
		}
		db, err := loadDB(dbPath)
		if err != nil {
			exitCode(4, "corrupt database", true)
		}
		type Result struct {
			Location
			DistanceM float64 `json:"distance_m"`
		}
		results := []Result{}
		for _, loc := range db {
			d := haversine(lat, lng, loc.Lat, loc.Lng)
			if d <= radius {
				results = append(results, Result{Location: loc, DistanceM: d})
			}
		}
		sort.Slice(results, func(i, j int) bool {
			if math.Abs(results[i].DistanceM-results[j].DistanceM) > 1e-9 {
				return results[i].DistanceM < results[j].DistanceM
			}
			return results[i].VehicleID < results[j].VehicleID
		})
		b, _ := json.Marshal(results)
		fmt.Println(string(b))

	case "clear":
		if err := saveDB(dbPath, make(map[string]Location)); err != nil {
			exitCode(2, fmt.Sprintf("clear failed: %v", err), true)
		}
		fmt.Println("cleared")

	default:
		exitCode(2, fmt.Sprintf("unknown command %s", cmd), true)
	}
}
GO
cd /app/src && go build -o locationctl . && echo "Step1 reference built"
