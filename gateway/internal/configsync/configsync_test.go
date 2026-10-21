package configsync

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"gateway/internal/allowlist"
)

// fakeStore 内存版 Store，附带 Pub/Sub 广播，供测试无需真实 Redis。
type fakeStore struct {
	mu          sync.Mutex
	sets        map[string][]string
	subs        map[string][]chan string
	failExists  bool
	failMembers bool
}

func newFakeStore() *fakeStore {
	return &fakeStore{sets: map[string][]string{}, subs: map[string][]chan string{}}
}

func (f *fakeStore) set(key string, members ...string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(members) == 0 {
		delete(f.sets, key)
		return
	}
	f.sets[key] = append([]string(nil), members...)
}

func (f *fakeStore) get(key string) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.sets[key]...)
}

func (f *fakeStore) Exists(_ context.Context, key string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failExists {
		return false, errors.New("exists failed")
	}
	_, ok := f.sets[key]
	return ok, nil
}

func (f *fakeStore) Members(_ context.Context, key string) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failMembers {
		return nil, errors.New("members failed")
	}
	return append([]string(nil), f.sets[key]...), nil
}

func (f *fakeStore) SeedIfAbsent(_ context.Context, key string, members []string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.sets[key]; ok {
		return nil // 已存在：不覆盖（与 Lua 脚本语义一致）
	}
	if len(members) > 0 {
		f.sets[key] = append([]string(nil), members...)
	}
	return nil
}

func (f *fakeStore) Subscribe(ctx context.Context, channel string) (<-chan string, error) {
	ch := make(chan string, 16)
	f.mu.Lock()
	f.subs[channel] = append(f.subs[channel], ch)
	f.mu.Unlock()
	go func() {
		<-ctx.Done()
		f.mu.Lock()
		chs := f.subs[channel]
		for i, c := range chs {
			if c == ch {
				f.subs[channel] = append(chs[:i], chs[i+1:]...)
				break
			}
		}
		close(ch)
		f.mu.Unlock()
	}()
	return ch, nil
}

// publish 持锁广播：与清理 goroutine（持锁移除+close）互斥，
// 保证不会向已关闭的 channel 发送。
func (f *fakeStore) publish(channel, payload string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, ch := range f.subs[channel] {
		select {
		case ch <- payload:
		default:
		}
	}
}

func waitFor(cond func() bool) bool {
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(5 * time.Millisecond)
	}
	return false
}

func newTestManager(f Store) *Manager {
	return New(Options{
		Store:         f,
		AllowlistKey:  "gw:llm_allowlist",
		KeywordsKey:   "gw:content_safety:keywords",
		Channel:       "gw:config_changed",
		SeedAllowlist: []string{"https://seed.com/v1"},
		SeedKeywords:  []string{"SeedWord"},
	})
}

func TestLoadSeedsRedisWhenAbsent(t *testing.T) {
	f := newFakeStore()
	m := newTestManager(f)
	m.Load(context.Background())

	if !allowlist.Match(m.AllowlistEntries(), "https://seed.com/v1/chat/completions") {
		t.Fatalf("seed 白名单未生效: %+v", m.AllowlistEntries())
	}
	if m.AllowlistSource() != "redis" {
		t.Fatalf("allowlist source=%q want redis", m.AllowlistSource())
	}
	// seed 已回写 Redis：之后 Redis 即单一数据源
	if got := f.get("gw:llm_allowlist"); len(got) != 1 || got[0] != "https://seed.com/v1" {
		t.Fatalf("seed 未回写 Redis: %v", got)
	}
	if got := m.Keywords(); len(got) != 1 || got[0] != "seedword" {
		t.Fatalf("seed 敏感词未生效/未规范化: %v", got)
	}
}

func TestLoadUsesRedisWhenPresent(t *testing.T) {
	f := newFakeStore()
	f.set("gw:llm_allowlist", "https://redis.com/v1")
	f.set("gw:content_safety:keywords", "rediskw")
	m := newTestManager(f)
	m.Load(context.Background())

	if !allowlist.Match(m.AllowlistEntries(), "https://redis.com/v1/x") {
		t.Fatalf("应采用 Redis 白名单: %+v", m.AllowlistEntries())
	}
	if allowlist.Match(m.AllowlistEntries(), "https://seed.com/v1/x") {
		t.Fatal("Redis 已有配置时不应回退 seed")
	}
	if got := m.Keywords(); len(got) != 1 || got[0] != "rediskw" {
		t.Fatalf("应采用 Redis 敏感词: %v", got)
	}
}

func TestLoadFallbackKeepsSeedOnStoreError(t *testing.T) {
	f := newFakeStore()
	f.failExists = true
	m := newTestManager(f)
	m.Load(context.Background())

	if !allowlist.Match(m.AllowlistEntries(), "https://seed.com/v1/x") {
		t.Fatal("Redis 故障应沿用 seed")
	}
	if m.AllowlistSource() != "seed" {
		t.Fatalf("故障时 source=%q want seed", m.AllowlistSource())
	}
}

func TestHotReloadAllowlistViaPublish(t *testing.T) {
	f := newFakeStore()
	f.set("gw:llm_allowlist", "https://old.com/v1")
	m := newTestManager(f)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m.Load(ctx)
	m.Start(ctx)

	// 管理端改 Redis 并 PUBLISH，副本应不重启即生效
	f.set("gw:llm_allowlist", "https://new.com/v1")
	f.publish("gw:config_changed", TopicAllowlist)

	if !waitFor(func() bool { return allowlist.Match(m.AllowlistEntries(), "https://new.com/v1/x") }) {
		t.Fatalf("白名单热更未生效: %+v", m.AllowlistEntries())
	}
	if allowlist.Match(m.AllowlistEntries(), "https://old.com/v1/x") {
		t.Fatal("旧白名单应被替换")
	}
}

func TestHotReloadKeywordsViaPublish(t *testing.T) {
	f := newFakeStore()
	f.set("gw:content_safety:keywords", "oldkw")
	m := newTestManager(f)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m.Load(ctx)
	m.Start(ctx)

	f.set("gw:content_safety:keywords", "NewKW")
	f.publish("gw:config_changed", TopicKeywords)

	if !waitFor(func() bool {
		kws := m.Keywords()
		return len(kws) == 1 && kws[0] == "newkw"
	}) {
		t.Fatalf("敏感词热更未生效: %v", m.Keywords())
	}
}

// TestReloadKeepsSnapshotOnError 防翻转核心：Redis 读取失败时保留旧快照，
// 绝不翻成"配置为空"（白名单空 = fail-closed 全拒，误翻转=事故）。
func TestReloadKeepsSnapshotOnError(t *testing.T) {
	f := newFakeStore()
	f.set("gw:llm_allowlist", "https://a.com/v1")
	m := newTestManager(f)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m.Load(ctx)
	m.Start(ctx)

	f.failMembers = true
	f.publish("gw:config_changed", TopicAllowlist)
	time.Sleep(150 * time.Millisecond) // 给 reload 尝试的机会

	if !allowlist.Match(m.AllowlistEntries(), "https://a.com/v1/x") {
		t.Fatal("Redis 故障必须保留旧快照，不得翻空")
	}
}

// TestReloadEmptyMeansReject 显式清空语义：管理员删空 Redis key 后，
// 快照更新为空（fail-closed 全拒）——这是主动操作的正确结果。
func TestReloadEmptyMeansReject(t *testing.T) {
	f := newFakeStore()
	f.set("gw:llm_allowlist", "https://a.com/v1", "https://b.com/v1")
	m := newTestManager(f)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m.Load(ctx)
	m.Start(ctx)

	f.set("gw:llm_allowlist") // 删除 key = 清空白名单
	f.publish("gw:config_changed", TopicAllowlist)

	if !waitFor(func() bool { return len(m.AllowlistEntries()) == 0 }) {
		t.Fatalf("显式清空后应为空（fail-closed）: %+v", m.AllowlistEntries())
	}
}

func TestNormalizeKeywords(t *testing.T) {
	got := normalizeKeywords([]string{"  Foo ", "foo", "", "BAR", "bar"})
	want := []string{"foo", "bar"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("normalizeKeywords = %v want %v", got, want)
	}
}

func TestNoStoreStaticSeed(t *testing.T) {
	m := New(Options{
		Store:         nil, // 无 Redis：仅静态 seed，不热更
		SeedAllowlist: []string{"https://a.com/v1"},
		SeedKeywords:  []string{"sw"},
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m.Load(ctx)  // no-op
	m.Start(ctx) // no-op

	if !allowlist.Match(m.AllowlistEntries(), "https://a.com/v1/x") {
		t.Fatal("无 store 应走 seed")
	}
	if m.AllowlistSource() != "seed" {
		t.Fatalf("source=%q want seed", m.AllowlistSource())
	}
}

func TestSeedIfAbsentDoesNotOverwrite(t *testing.T) {
	f := newFakeStore()
	f.set("gw:llm_allowlist", "https://existing.com/v1")
	if err := f.SeedIfAbsent(context.Background(), "gw:llm_allowlist", []string{"https://seed.com/v1"}); err != nil {
		t.Fatal(err)
	}
	if got := f.get("gw:llm_allowlist"); len(got) != 1 || got[0] != "https://existing.com/v1" {
		t.Fatalf("SeedIfAbsent 不得覆盖已存在配置: %v", got)
	}
}
