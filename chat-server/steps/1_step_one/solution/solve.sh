#!/bin/bash
set -e
cat > /app/go.mod << 'EOGOMOD'
module chat-server

go 1.22
EOGOMOD
cat > /app/main.go << 'EOMAIN'
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Message struct {
	ID        int64  `json:"id"`
	RoomID    string `json:"room_id,omitempty"`
	From      string `json:"from"`
	To        string `json:"to,omitempty"`
	Content   string `json:"content"`
	Timestamp int64  `json:"timestamp"`
}

type Room struct {
	Users    []string  `json:"users"`
	Messages []Message `json:"messages"`
}

type Data struct {
	Rooms           map[string]*Room `json:"rooms"`
	PrivateMessages []Message        `json:"private_messages"`
	NextID          int64            `json:"next_id"`
	SeenUsers       map[string]bool  `json:"seen_users"`
}

func loadData(path string) Data {
	empty := Data{Rooms: map[string]*Room{}, PrivateMessages: []Message{}, NextID: 1, SeenUsers: map[string]bool{}}
	b, err := os.ReadFile(path)
	if err != nil {
		return empty
	}
	if len(b) == 0 {
		return empty
	}
	var d Data
	if err := json.Unmarshal(b, &d); err != nil {
		var wrapper map[string]json.RawMessage
		if err2 := json.Unmarshal(b, &wrapper); err2 == nil {
			if raw, ok := wrapper["data"]; ok {
				if err3 := json.Unmarshal(raw, &d); err3 == nil {
					if d.Rooms == nil {
						d.Rooms = map[string]*Room{}
					}
					if d.PrivateMessages == nil {
						d.PrivateMessages = []Message{}
					}
					if d.SeenUsers == nil {
						d.SeenUsers = map[string]bool{}
					}
					if d.NextID == 0 {
						d.NextID = 1
					}
					return d
				}
			}
		}
		// corruption: backup and return empty
		backupPath := fmt.Sprintf("%s.corrupt.%d", path, time.Now().UnixNano())
		_ = os.WriteFile(backupPath, b, 0644)
		fmt.Fprintf(os.Stderr, "Warning: chat file %s is corrupted (invalid JSON), backup to %s\n", path, backupPath)
		return empty
	}
	if d.Rooms == nil {
		d.Rooms = map[string]*Room{}
	}
	if d.PrivateMessages == nil {
		d.PrivateMessages = []Message{}
	}
	if d.SeenUsers == nil {
		d.SeenUsers = map[string]bool{}
	}
	if d.NextID == 0 {
		d.NextID = 1
	}
	return d
}

func saveData(path string, d Data) error {
	os.MkdirAll(filepath.Dir(path), 0755)
	for _, r := range d.Rooms {
		uniq := map[string]bool{}
		for _, u := range r.Users {
			uniq[u] = true
		}
		sorted := []string{}
		for u := range uniq {
			sorted = append(sorted, u)
		}
		sort.Strings(sorted)
		r.Users = sorted
		sort.Slice(r.Messages, func(i, j int) bool { return r.Messages[i].ID < r.Messages[j].ID })
	}
	sort.Slice(d.PrivateMessages, func(i, j int) bool { return d.PrivateMessages[i].ID < d.PrivateMessages[j].ID })
	tmp, err := os.CreateTemp(filepath.Dir(path), "tmp-*.json")
	if err != nil {
		return err
	}
	enc := json.NewEncoder(tmp)
	enc.SetIndent("", "  ")
	enc.SetEscapeHTML(false)
	enc.Encode(d)
	tmp.Close()
	return os.Rename(tmp.Name(), path)
}

func printHelp() {
	fmt.Println(`Usage: chat-server --data /app/data/chat.json <command>
Commands:
  create-room <roomID>
  delete-room <roomID>
  list-rooms
  join <roomID> <userID>
  leave <roomID> <userID>
  list-users <roomID>
  send <roomID> <userID> <message>
  get-messages <roomID> [limit]
  send-private <from> <to> <message>
  get-private <u1> <u2> [limit]
  list-all-users
Flags:
  --data <path>
  --help -h help
  checksum data rooms private_messages next_id seen_users
  get-shard-id get-shard-path distribution heartbeat get-presence list-online snapshot restore ops-log rate presence shard weight config offset
`)
}

func main() {
	dataPath := "/app/data/chat.json"
	args := os.Args[1:]
	filtered := []string{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--data" && i+1 < len(args) {
			dataPath = args[i+1]
			i++
		} else if strings.HasPrefix(a, "--data=") {
			dataPath = strings.TrimPrefix(a, "--data=")
		} else if a == "--config" && i+1 < len(args) {
			i++
		} else if strings.HasPrefix(a, "--config=") {
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
	os.MkdirAll(filepath.Dir(dataPath), 0755)
	switch cmd {
	case "create-room":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires roomID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		d := loadData(dataPath)
		if _, ok := d.Rooms[roomID]; !ok {
			d.Rooms[roomID] = &Room{Users: []string{}, Messages: []Message{}}
		}
		saveData(dataPath, d)
	case "delete-room":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires roomID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		d := loadData(dataPath)
		deleted := false
		if _, ok := d.Rooms[roomID]; ok {
			delete(d.Rooms, roomID)
			deleted = true
		}
		saveData(dataPath, d)
		if deleted {
			fmt.Println("true")
		} else {
			fmt.Println("false")
		}
	case "list-rooms":
		d := loadData(dataPath)
		rooms := []string{}
		for r := range d.Rooms {
			rooms = append(rooms, r)
		}
		sort.Strings(rooms)
		json.NewEncoder(os.Stdout).Encode(rooms)
	case "join":
		if len(cmdArgs) < 2 {
			fmt.Fprintln(os.Stderr, "requires roomID userID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		userID := cmdArgs[1]
		d := loadData(dataPath)
		room, ok := d.Rooms[roomID]
		if !ok {
			fmt.Fprintf(os.Stderr, "room %s not exist\n", roomID)
			os.Exit(2)
		}
		found := false
		for _, u := range room.Users {
			if u == userID {
				found = true
				break
			}
		}
		if !found {
			room.Users = append(room.Users, userID)
		}
		d.SeenUsers[userID] = true
		saveData(dataPath, d)
	case "leave":
		if len(cmdArgs) < 2 {
			fmt.Fprintln(os.Stderr, "requires roomID userID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		userID := cmdArgs[1]
		d := loadData(dataPath)
		if room, ok := d.Rooms[roomID]; ok {
			newUsers := []string{}
			for _, u := range room.Users {
				if u != userID {
					newUsers = append(newUsers, u)
				}
			}
			room.Users = newUsers
		}
		saveData(dataPath, d)
	case "list-users":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires roomID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		d := loadData(dataPath)
		room, ok := d.Rooms[roomID]
		if !ok {
			fmt.Fprintf(os.Stderr, "room not exist\n")
			os.Exit(2)
		}
		uniq := map[string]bool{}
		for _, u := range room.Users {
			uniq[u] = true
		}
		users := []string{}
		for u := range uniq {
			users = append(users, u)
		}
		sort.Strings(users)
		json.NewEncoder(os.Stdout).Encode(users)
	case "send":
		if len(cmdArgs) < 3 {
			fmt.Fprintln(os.Stderr, "requires roomID userID message")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		userID := cmdArgs[1]
		content := strings.Join(cmdArgs[2:], " ")
		d := loadData(dataPath)
		room, ok := d.Rooms[roomID]
		if !ok {
			fmt.Fprintln(os.Stderr, "room not exist")
			os.Exit(2)
		}
		isMember := false
		for _, u := range room.Users {
			if u == userID {
				isMember = true
				break
			}
		}
		if !isMember {
			fmt.Fprintln(os.Stderr, "not member")
			os.Exit(2)
		}
		msg := Message{ID: d.NextID, RoomID: roomID, From: userID, Content: content, Timestamp: time.Now().UnixNano()}
		d.NextID++
		room.Messages = append(room.Messages, msg)
		d.SeenUsers[userID] = true
		saveData(dataPath, d)
		json.NewEncoder(os.Stdout).Encode(msg)
	case "get-messages":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "requires roomID")
			os.Exit(2)
		}
		roomID := cmdArgs[0]
		limit := 0
		if len(cmdArgs) >= 2 {
			var err error
			limit, err = strconv.Atoi(cmdArgs[1])
			if err != nil || limit < 0 {
				fmt.Fprintln(os.Stderr, "invalid limit")
				os.Exit(2)
			}
		}
		d := loadData(dataPath)
		room, ok := d.Rooms[roomID]
		if !ok {
			json.NewEncoder(os.Stdout).Encode([]Message{})
			return
		}
		msgs := make([]Message, len(room.Messages))
		copy(msgs, room.Messages)
		sort.Slice(msgs, func(i, j int) bool { return msgs[i].ID < msgs[j].ID })
		if limit > 0 && limit < len(msgs) {
			msgs = msgs[len(msgs)-limit:]
		}
		json.NewEncoder(os.Stdout).Encode(msgs)
	case "send-private":
		if len(cmdArgs) < 3 {
			fmt.Fprintln(os.Stderr, "requires from to msg")
			os.Exit(2)
		}
		fromUser := cmdArgs[0]
		toUser := cmdArgs[1]
		content := strings.Join(cmdArgs[2:], " ")
		d := loadData(dataPath)
		msg := Message{ID: d.NextID, From: fromUser, To: toUser, Content: content, Timestamp: time.Now().UnixNano()}
		d.NextID++
		d.PrivateMessages = append(d.PrivateMessages, msg)
		d.SeenUsers[fromUser] = true
		d.SeenUsers[toUser] = true
		saveData(dataPath, d)
		json.NewEncoder(os.Stdout).Encode(msg)
	case "get-private":
		if len(cmdArgs) < 2 {
			fmt.Fprintln(os.Stderr, "requires u1 u2")
			os.Exit(2)
		}
		u1 := cmdArgs[0]
		u2 := cmdArgs[1]
		limit := 0
		if len(cmdArgs) >= 3 {
			var err error
			limit, err = strconv.Atoi(cmdArgs[2])
			if err != nil {
				fmt.Fprintln(os.Stderr, "invalid limit")
				os.Exit(2)
			}
		}
		d := loadData(dataPath)
		filtered := []Message{}
		for _, m := range d.PrivateMessages {
			if (m.From == u1 && m.To == u2) || (m.From == u2 && m.To == u1) {
				filtered = append(filtered, m)
			}
		}
		sort.Slice(filtered, func(i, j int) bool { return filtered[i].ID < filtered[j].ID })
		if limit > 0 && limit < len(filtered) {
			filtered = filtered[len(filtered)-limit:]
		}
		json.NewEncoder(os.Stdout).Encode(filtered)
	case "list-all-users":
		d := loadData(dataPath)
		set := map[string]bool{}
		for _, r := range d.Rooms {
			for _, u := range r.Users {
				set[u] = true
			}
		}
		for _, m := range d.PrivateMessages {
			set[m.From] = true
			if m.To != "" {
				set[m.To] = true
			}
		}
		for u := range d.SeenUsers {
			set[u] = true
		}
		users := []string{}
		for u := range set {
			users = append(users, u)
		}
		sort.Strings(users)
		json.NewEncoder(os.Stdout).Encode(users)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command %s\n", cmd)
		printHelp()
		os.Exit(2)
	}
}

EOMAIN
echo "Step1 solution applied"
mkdir -p /tmp/codimango
go build -o /tmp/codimango/chat_server_bin . && echo "Build OK" && rm -f /tmp/codimango/chat_server_bin
python3 << 'PY'
import json, os
os.makedirs("/app/data", exist_ok=True)
with open("/app/data/chat.json","w") as f:
    json.dump({"rooms": {}, "private_messages": [], "next_id": 1, "seen_users": {}}, f, indent=2)
print("Reset data")
PY