package metrics

import (
	"sync"
	"testing"
)

// TestIncAndSnapshot：基本计数与快照。
func TestIncAndSnapshot(t *testing.T) {
	Reset()
	Inc(KeyRequests)
	Inc(KeyRequests)
	Add(KeyHTTP5xx, 3)

	snap := Snapshot()
	if snap[KeyRequests] != 2 || snap[KeyHTTP5xx] != 3 {
		t.Fatalf("计数不符: %v", snap)
	}
}

// TestUnknownKeyIgnored：白名单外的键必须被忽略（隐私红线：
// 防止有人把 path/uid 当 key 传进去回传）。
func TestUnknownKeyIgnored(t *testing.T) {
	Reset()
	Inc("/api/app/solve?uid=123")
	Inc("some_random_key")
	Inc("")

	if len(Snapshot()) != 0 {
		t.Fatalf("白名单外键应被忽略: %v", Snapshot())
	}
}

// TestConcurrentInc：并发计数不丢（最终值必须精确）。
func TestConcurrentInc(t *testing.T) {
	Reset()
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				Inc(KeyRequests)
			}
		}()
	}
	wg.Wait()
	if got := Snapshot()[KeyRequests]; got != 5000 {
		t.Fatalf("并发计数应精确 5000，实际 %d", got)
	}
}

// TestKeysSorted：白名单键有序（telemetry 序列化稳定）。
func TestKeysSorted(t *testing.T) {
	keys := Keys()
	for i := 1; i < len(keys); i++ {
		if keys[i-1] > keys[i] {
			t.Fatalf("键未排序: %v", keys)
		}
	}
	if len(keys) != 11 {
		t.Fatalf("白名单应有 11 个键，实际 %d", len(keys))
	}
}
