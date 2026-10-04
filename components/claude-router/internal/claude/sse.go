package claude

import (
	"bytes"
	"encoding/json"
	"strings"
)

type sseEvent struct {
	Name string
	Data []byte
}

type sseScanner struct {
	buf  []byte
	name string
	data [][]byte
}

func (s *sseScanner) feed(chunk []byte) []sseEvent {
	s.buf = append(s.buf, chunk...)
	var out []sseEvent
	for {
		i := bytes.IndexByte(s.buf, '\n')
		if i < 0 {
			return out
		}
		line := bytes.TrimRight(s.buf[:i], "\r")
		s.buf = s.buf[i+1:]
		switch {
		case len(line) == 0:
			if s.name != "" || len(s.data) > 0 {
				name := s.name
				if name == "" {
					name = "message"
				}
				out = append(out, sseEvent{Name: name, Data: bytes.Join(s.data, []byte("\n"))})
			}
			s.name, s.data = "", nil
		case bytes.HasPrefix(line, []byte("event:")):
			s.name = strings.TrimSpace(string(line[len("event:"):]))
		case bytes.HasPrefix(line, []byte("data:")):
			s.data = append(s.data, bytes.Clone(bytes.TrimPrefix(bytes.TrimPrefix(line, []byte("data:")), []byte(" "))))
		}
	}
}

func usageFrom(name string, data []byte) (Usage, bool) {
	var shape struct {
		Usage   *usageJSON `json:"usage"`
		Message struct {
			Usage *usageJSON `json:"usage"`
		} `json:"message"`
	}
	if json.Unmarshal(data, &shape) != nil {
		return Usage{}, false
	}
	u := shape.Usage
	if name == "message_start" {
		u = shape.Message.Usage
	}
	if u == nil {
		return Usage{}, false
	}
	return Usage{
		InputTokens:              u.InputTokens,
		OutputTokens:             u.OutputTokens,
		CacheCreationInputTokens: u.CacheCreationInputTokens,
		CacheReadInputTokens:     u.CacheReadInputTokens,
	}, true
}

type usageJSON struct {
	InputTokens              *int64 `json:"input_tokens"`
	OutputTokens             *int64 `json:"output_tokens"`
	CacheCreationInputTokens *int64 `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     *int64 `json:"cache_read_input_tokens"`
}

func streamErrorType(data []byte) string {
	var e struct {
		Error struct {
			Type string `json:"type"`
		} `json:"error"`
	}
	_ = json.Unmarshal(data, &e)
	return e.Error.Type
}
