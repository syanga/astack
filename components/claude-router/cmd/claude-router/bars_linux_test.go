package main

import (
	"fmt"
	"os"
	"syscall"
	"testing"
	"unsafe"
)

func openTerminal(t *testing.T, cols uint16) *os.File {
	t.Helper()
	ptmx, err := os.OpenFile("/dev/ptmx", os.O_RDWR, 0)
	if err != nil {
		t.Skipf("no pseudo-terminal: %v", err)
	}
	t.Cleanup(func() { ptmx.Close() })
	ioctl := func(req uintptr, arg unsafe.Pointer) {
		if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, ptmx.Fd(), req, uintptr(arg)); errno != 0 {
			t.Fatalf("ioctl %#x: %v", req, errno)
		}
	}
	var unlock int32
	ioctl(syscall.TIOCSPTLCK, unsafe.Pointer(&unlock))
	var n uint32
	ioctl(syscall.TIOCGPTN, unsafe.Pointer(&n))
	size := struct{ rows, cols, x, y uint16 }{rows: 40, cols: cols}
	ioctl(syscall.TIOCSWINSZ, unsafe.Pointer(&size))
	tty, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", n), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { tty.Close() })
	return tty
}

func TestStatusDrawsBarsOnlyWhenItsOutputIsATerminal(t *testing.T) {
	env := func(vars map[string]string) func(string) string {
		return func(k string) string { return vars[k] }
	}
	tty := openTerminal(t, 132)
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer pr.Close()
	defer pw.Close()
	null, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer null.Close()

	for _, tc := range []struct {
		name string
		f    *os.File
		vars map[string]string
		want *screen
	}{
		{"terminal", tty, nil, &screen{width: 132, color: true}},
		{"terminal with NO_COLOR", tty, map[string]string{"NO_COLOR": "1"}, &screen{width: 132, color: false}},
		{"terminal with TERM=dumb", tty, map[string]string{"TERM": "dumb"}, nil},
		{"pipe", pw, nil, nil},
		{os.DevNull, null, nil, nil},
	} {
		got := terminalScreen(tc.f, env(tc.vars))
		if (got == nil) != (tc.want == nil) || (got != nil && *got != *tc.want) {
			t.Errorf("%s: screen %+v, want %+v", tc.name, got, tc.want)
		}
	}
}
