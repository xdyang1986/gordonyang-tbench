package autoscaler

import "time"

// Clock abstracts time so the stabilization windows can be tested
// deterministically without sleeping.
type Clock interface {
	Now() time.Time
}

// realClock is the production Clock backed by the system time.
type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }

// FakeClock is a manually-advanced Clock for tests.
type FakeClock struct {
	t time.Time
}

// NewFakeClock returns a FakeClock anchored at the given time.
func NewFakeClock(start time.Time) *FakeClock { return &FakeClock{t: start} }

// Now returns the clock's current time.
func (f *FakeClock) Now() time.Time { return f.t }

// Advance moves the clock forward by d.
func (f *FakeClock) Advance(d time.Duration) { f.t = f.t.Add(d) }
