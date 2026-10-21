// Package configsync 提供 LLM 白名单与敏感词表的 Redis 化动态配置。
//
// 设计要点：
//   - Redis 为单一数据源。config.yaml 的 llm_allowlist.endpoints /
//     content_safety.keywords 仅作首次启动的初始 seed，之后以 Redis 为准；
//   - 启动时 Load：Redis 已有配置则采用；key 缺失则把 seed 经 Lua 脚本
//     原子回写（仅 key 不存在时写，不覆盖管理端配置）后采用；
//   - 运行时热更新：订阅 Pub/Sub 频道，管理端改完 Redis 后 PUBLISH
//     通知，各副本立即重载，无需重启（多副本自动一致）；
//   - 防翻转：热重载时 Redis 读取失败一律保留上一份快照，
//     绝不因故障翻成"配置为空"（白名单空 = fail-closed 全拒，误翻转=事故）。
//
// 中间件通过 AllowlistEntries()/Keywords() 每请求读取当前生效值，
// 两值均为原子快照，热替换对在途请求无锁、安全。
package configsync

import (
	"context"
	"log"
	"strings"
	"sync/atomic"

	"github.com/redis/go-redis/v9"

	"gateway/internal/allowlist"
)

// Pub/Sub 消息负载（管理端 PUBLISH 时使用）。
const (
	TopicAllowlist = "llm_allowlist"  // 仅重载白名单
	TopicKeywords  = "content_safety" // 仅重载敏感词
	// 其它/空负载：全量重载
)

// Store 抽象配置存储。默认实现为 redisStore（go-redis，复用限流同一 client）；
// 测试用内存 fake，无需真实 Redis。
type Store interface {
	// Exists 判断 key 是否存在。
	Exists(ctx context.Context, key string) (bool, error)
	// Members 返回 set 成员；key 不存在时返回空切片、无错误。
	Members(ctx context.Context, key string) ([]string, error)
	// SeedIfAbsent 当且仅当 key 不存在时写入 members（原子）；已存在则不动。
	SeedIfAbsent(ctx context.Context, key string, members []string) error
	// Subscribe 订阅频道并返回消息通道；ctx 取消或通道关闭即结束。
	Subscribe(ctx context.Context, channel string) (<-chan string, error)
}

// Options 构造 Manager 的参数。
type Options struct {
	Store         Store    // 存储后端；nil 表示无 Redis，仅用 seed 静态值（不热更）
	AllowlistKey  string   // 白名单 Redis key，如 "gw:llm_allowlist"
	KeywordsKey   string   // 敏感词 Redis key，如 "gw:content_safety:keywords"
	Channel       string   // Pub/Sub 频道，如 "gw:config_changed"
	SeedAllowlist []string // yaml 初始白名单（原始 endpoint 串）
	SeedKeywords  []string // yaml 初始敏感词（原始串）
	Logger        *log.Logger
}

// Manager 持有一对原子快照（白名单条目 / 敏感词），
// 中间件经 AllowlistEntries()/Keywords() 每请求读取当前生效值。
type Manager struct {
	opt    Options
	logger *log.Logger

	entries atomic.Pointer[[]allowlist.Entry]
	kws     atomic.Pointer[[]string]

	// 数据来源标记（运维可观测）："redis" / "seed"
	allowlistSrc atomic.Value
	keywordsSrc  atomic.Value
}

// New 构造 Manager 并立即置入 seed 快照，
// 保证 Load 失败或未调用时也有确定值（而不是 nil 导致 panic）。
func New(opt Options) *Manager {
	m := &Manager{opt: opt}
	if opt.Logger != nil {
		m.logger = opt.Logger
	} else {
		m.logger = log.Default()
	}
	entries, err := allowlist.ParseEndpoints(opt.SeedAllowlist)
	if err != nil {
		// seed 非法：config.Load 已 fail fast，这里兜底为空白名单
		m.logger.Printf("configsync: seed allowlist parse error: %v", err)
		entries = nil
	}
	m.entries.Store(&entries)
	kws := normalizeKeywords(opt.SeedKeywords)
	m.kws.Store(&kws)
	m.allowlistSrc.Store("seed")
	m.keywordsSrc.Store("seed")
	return m
}

// AllowlistEntries 返回当前生效的白名单条目（每请求调用，热更新安全）。
func (m *Manager) AllowlistEntries() []allowlist.Entry {
	return *m.entries.Load()
}

// Keywords 返回当前生效的敏感词表（已规范化为小写；每请求调用，热更新安全）。
func (m *Manager) Keywords() []string {
	return *m.kws.Load()
}

// AllowlistSource / KeywordsSource 返回各项当前数据来源（"redis"/"seed"），
// 供启动日志与运维观测（排障时确认热更链路是否真的走了 Redis）。
func (m *Manager) AllowlistSource() string { return srcOf(&m.allowlistSrc) }
func (m *Manager) KeywordsSource() string  { return srcOf(&m.keywordsSrc) }

func srcOf(v *atomic.Value) string {
	s, _ := v.Load().(string)
	if s == "" {
		return "seed"
	}
	return s
}

// Load 初始加载：Redis 优先；key 缺失则原子回写 seed 后采用 seed；
// Redis 故障则沿用 seed（不致命——白名单 fail-closed 语义由 entries 快照保证）。
func (m *Manager) Load(ctx context.Context) {
	m.loadAllowlist(ctx)
	m.loadKeywords(ctx)
}

func (m *Manager) loadAllowlist(ctx context.Context) {
	if m.opt.Store == nil {
		return
	}
	exists, err := m.opt.Store.Exists(ctx, m.opt.AllowlistKey)
	if err != nil {
		m.logger.Printf("configsync: allowlist EXISTS %s failed: %v（沿用 seed）", m.opt.AllowlistKey, err)
		return
	}
	var members []string
	if exists {
		members, err = m.opt.Store.Members(ctx, m.opt.AllowlistKey)
		if err != nil {
			m.logger.Printf("configsync: allowlist SMEMBERS %s failed: %v（沿用 seed）", m.opt.AllowlistKey, err)
			return
		}
	} else {
		// key 缺失：把 seed 回写为初始配置，之后 Redis 即单一数据源。
		// 用 Lua 原子完成"仅不存在时写"，多副本并发冷启动不会互相覆盖。
		if err := m.opt.Store.SeedIfAbsent(ctx, m.opt.AllowlistKey, m.opt.SeedAllowlist); err != nil {
			m.logger.Printf("configsync: allowlist seed %s failed: %v（沿用 seed）", m.opt.AllowlistKey, err)
			return
		}
		members = m.opt.SeedAllowlist
	}
	entries, err := allowlist.ParseEndpoints(members)
	if err != nil {
		m.logger.Printf("configsync: allowlist %s 含非法 endpoint: %v（沿用 seed）", m.opt.AllowlistKey, err)
		return
	}
	m.entries.Store(&entries)
	m.allowlistSrc.Store("redis")
	m.logger.Printf("configsync: allowlist loaded from redis: %d endpoint(s)", len(entries))
}

func (m *Manager) loadKeywords(ctx context.Context) {
	if m.opt.Store == nil {
		return
	}
	exists, err := m.opt.Store.Exists(ctx, m.opt.KeywordsKey)
	if err != nil {
		m.logger.Printf("configsync: keywords EXISTS %s failed: %v（沿用 seed）", m.opt.KeywordsKey, err)
		return
	}
	var members []string
	if exists {
		members, err = m.opt.Store.Members(ctx, m.opt.KeywordsKey)
		if err != nil {
			m.logger.Printf("configsync: keywords SMEMBERS %s failed: %v（沿用 seed）", m.opt.KeywordsKey, err)
			return
		}
	} else {
		if err := m.opt.Store.SeedIfAbsent(ctx, m.opt.KeywordsKey, m.opt.SeedKeywords); err != nil {
			m.logger.Printf("configsync: keywords seed %s failed: %v（沿用 seed）", m.opt.KeywordsKey, err)
			return
		}
		members = m.opt.SeedKeywords
	}
	kws := normalizeKeywords(members)
	m.kws.Store(&kws)
	m.keywordsSrc.Store("redis")
	m.logger.Printf("configsync: keywords loaded from redis: %d keyword(s)", len(kws))
}

// Start 订阅 Pub/Sub 频道，收到通知后热重载对应配置。
// 生命周期由 ctx 控制（main 的优雅退出 ctx），无需单独 Stop。
func (m *Manager) Start(ctx context.Context) {
	if m.opt.Store == nil {
		return
	}
	ch, err := m.opt.Store.Subscribe(ctx, m.opt.Channel)
	if err != nil {
		m.logger.Printf("configsync: subscribe %s failed: %v（热更新不可用，沿用当前快照）", m.opt.Channel, err)
		return
	}
	m.logger.Printf("configsync: subscribed %s（热更新就绪）", m.opt.Channel)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case payload, ok := <-ch:
				if !ok {
					return
				}
				m.reload(ctx, payload)
			}
		}
	}()
}

// reload 按负载热重载。未知负载按全量处理（容忍不同的发布约定）。
func (m *Manager) reload(ctx context.Context, topic string) {
	switch topic {
	case TopicAllowlist:
		m.reloadAllowlist(ctx)
	case TopicKeywords:
		m.reloadKeywords(ctx)
	default:
		m.reloadAllowlist(ctx)
		m.reloadKeywords(ctx)
	}
}

// reloadAllowlist 热重载白名单。防翻转语义：
//   - 读取失败 / 含非法 endpoint → 保留旧快照（故障或误写不翻转配置）；
//   - 读到空（key 不存在或空 set）→ 更新为空（管理员显式清空，
//     fail-closed 全拒语义正确；Redis 侧管控 + 密码 + 审计防误删）。
func (m *Manager) reloadAllowlist(ctx context.Context) {
	if m.opt.Store == nil {
		return
	}
	members, err := m.opt.Store.Members(ctx, m.opt.AllowlistKey)
	if err != nil {
		m.logger.Printf("configsync: reload allowlist failed: %v（保留旧快照，防翻转）", err)
		return
	}
	entries, err := allowlist.ParseEndpoints(members)
	if err != nil {
		m.logger.Printf("configsync: reload allowlist 含非法 endpoint: %v（保留旧快照）", err)
		return
	}
	prev := len(m.AllowlistEntries())
	m.entries.Store(&entries)
	m.allowlistSrc.Store("redis")
	m.logger.Printf("configsync: allowlist hot-reloaded: %d -> %d endpoint(s)", prev, len(entries))
}

// reloadKeywords 热重载敏感词（防翻转语义同 reloadAllowlist）。
func (m *Manager) reloadKeywords(ctx context.Context) {
	if m.opt.Store == nil {
		return
	}
	members, err := m.opt.Store.Members(ctx, m.opt.KeywordsKey)
	if err != nil {
		m.logger.Printf("configsync: reload keywords failed: %v（保留旧快照，防翻转）", err)
		return
	}
	kws := normalizeKeywords(members)
	prev := len(m.Keywords())
	m.kws.Store(&kws)
	m.keywordsSrc.Store("redis")
	m.logger.Printf("configsync: keywords hot-reloaded: %d -> %d keyword(s)", prev, len(kws))
}

// normalizeKeywords 规范化敏感词：小写 + trim + 去空 + 去重。
// 供 seed 与 Redis 数据共用，保证两条路径词表形态一致。
func normalizeKeywords(raw []string) []string {
	seen := make(map[string]struct{}, len(raw))
	out := make([]string, 0, len(raw))
	for _, k := range raw {
		k = strings.ToLower(strings.TrimSpace(k))
		if k == "" {
			continue
		}
		if _, dup := seen[k]; dup {
			continue
		}
		seen[k] = struct{}{}
		out = append(out, k)
	}
	return out
}

// ---- Redis 实现 ----

// seedScript 原子回写 seed：仅当 key 不存在时 SADD。
// 多副本并发冷启动时只有一个副本写成功，且不会覆盖管理端既有配置。
var seedScript = redis.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then
  redis.call('SADD', KEYS[1], unpack(ARGV))
  return 1
end
return 0
`)

// redisStore 基于 go-redis 的 Store 实现，复用限流同一个 client
// （client 的关闭由 main 统一负责，本类型不持有关闭职责）。
type redisStore struct {
	rdb *redis.Client
}

// NewRedisStore 用已有 client 构造 Store；rdb 为 nil 时返回 nil。
func NewRedisStore(rdb *redis.Client) Store {
	if rdb == nil {
		return nil
	}
	return &redisStore{rdb: rdb}
}

func (s *redisStore) Exists(ctx context.Context, key string) (bool, error) {
	n, err := s.rdb.Exists(ctx, key).Result()
	return n > 0, err
}

func (s *redisStore) Members(ctx context.Context, key string) ([]string, error) {
	return s.rdb.SMembers(ctx, key).Result()
}

func (s *redisStore) SeedIfAbsent(ctx context.Context, key string, members []string) error {
	if len(members) == 0 {
		return nil // 空 seed 不建 key，保持"未配置"语义清晰
	}
	return seedScript.Run(ctx, s.rdb, []string{key}, toIface(members)...).Err()
}

func (s *redisStore) Subscribe(ctx context.Context, channel string) (<-chan string, error) {
	sub := s.rdb.Subscribe(ctx, channel)
	if _, err := sub.Receive(ctx); err != nil {
		_ = sub.Close()
		return nil, err
	}
	out := make(chan string, 8)
	go func() {
		defer close(out)
		src := sub.Channel()
		for {
			select {
			case <-ctx.Done():
				_ = sub.Close()
				return
			case msg, ok := <-src:
				if !ok {
					return
				}
				select {
				case out <- msg.Payload:
				case <-ctx.Done():
					_ = sub.Close()
					return
				}
			}
		}
	}()
	return out, nil
}

func toIface(ss []string) []interface{} {
	out := make([]interface{}, len(ss))
	for i, s := range ss {
		out[i] = s
	}
	return out
}
