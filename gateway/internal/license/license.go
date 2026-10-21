// Package license 实现授权/更新回连心跳（P5 合同硬要求 2/3）：
// 我方侧 "授权/更新回连：心跳 ≤100KB/天"。
//
// 行为：
//   - 后台 goroutine 按 Interval 周期性 POST 匿名心跳（instance_id + 版本 + 计数）；
//   - 跟踪 lastAttempt/lastSuccess/连续失败次数，派生状态 ok/degraded；
//   - degraded（连续失败达阈值）打告警日志；恢复时打 info；
//   - 状态经 /licensez（仅本机/内网）暴露，供运维查看授权健康度；
//   - 心跳失败绝不影响请求转发（纯旁路，失败只记日志）。
//
// 隐私：心跳体只含匿名实例 ID、网关版本、单调计数器——不含任何学生内容。
package license

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Options 心跳选项。
type Options struct {
	// Enabled 总开关。
	Enabled bool
	// URL 心跳接收端（我方 license 服务）。
	URL string
	// Interval 心跳周期。默认 6h（4 次/天 × ~300B ≈ 1.2KB/天，远低于 100KB 预算）。
	Interval time.Duration
	// Timeout 单次心跳超时。默认 5s。
	Timeout time.Duration
	// DegradedAfter 连续失败多少次进入 degraded。默认 3。
	DegradedAfter int
	// InstanceID 匿名实例标识（部署时生成，不关联学校/学生）。
	InstanceID string
	// Version 网关版本号。
	Version string
	// Client 可注入的 HTTP 客户端（测试用）。
	Client *http.Client
	// OnStateChange 状态迁移回调（ok<->degraded），供测试/告警钩子。
	OnStateChange func(from, to string)
}

// Status 心跳状态快照。
type Status struct {
	State              string     `json:"state"` // ok / degraded / starting
	LastAttempt        *time.Time `json:"last_attempt,omitempty"`
	LastSuccess        *time.Time `json:"last_success,omitempty"`
	ConsecutiveFailure int        `json:"consecutive_failures"`
	TotalSent          int64      `json:"total_sent"`
	TotalFailed        int64      `json:"total_failed"`
	LastPayloadBytes   int        `json:"last_payload_bytes"`
}

// Heartbeat 心跳器。
type Heartbeat struct {
	opt Options

	mu     sync.Mutex
	status Status
	stopCh chan struct{}
	wg     sync.WaitGroup
}

// New 构造心跳器并补全默认值。
func New(opt Options) *Heartbeat {
	if opt.Interval <= 0 {
		opt.Interval = 6 * time.Hour
	}
	if opt.Timeout <= 0 {
		opt.Timeout = 5 * time.Second
	}
	if opt.DegradedAfter <= 0 {
		opt.DegradedAfter = 3
	}
	if opt.Client == nil {
		opt.Client = &http.Client{Timeout: opt.Timeout}
	}
	return &Heartbeat{opt: opt, status: Status{State: "starting"}, stopCh: make(chan struct{})}
}

// Start 启动后台心跳循环（幂等保护由调用方保证只调一次）。
// ctx 取消时退出；Stop 可提前终止。
func (h *Heartbeat) Start(ctx context.Context) {
	h.wg.Add(1)
	go func() {
		defer h.wg.Done()
		// 启动后先立即心跳一次（部署即注册），再按周期
		h.beat()
		ticker := time.NewTicker(h.opt.Interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-h.stopCh:
				return
			case <-ticker.C:
				h.beat()
			}
		}
	}()
}

// Stop 停止心跳并等待退出。
func (h *Heartbeat) Stop() {
	close(h.stopCh)
	h.wg.Wait()
}

// Status 返回当前状态快照。
func (h *Heartbeat) Status() Status {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.status
}

// beat 执行一次心跳（导出仅用于测试手动触发）。
func (h *Heartbeat) beat() {
	payload := h.buildPayload()
	body, err := json.Marshal(payload)
	if err != nil {
		log.Printf("[license] marshal payload: %v", err)
		return
	}

	now := time.Now()
	h.mu.Lock()
	h.status.LastAttempt = &now
	h.status.LastPayloadBytes = len(body)
	h.mu.Unlock()

	ctx, cancel := context.WithTimeout(context.Background(), h.opt.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.opt.URL, bytes.NewReader(body))
	if err != nil {
		h.recordFailure(err)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := h.opt.Client.Do(req)
	if err != nil {
		h.recordFailure(err)
		return
	}
	_ = resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		h.recordFailure(errStatus(resp.StatusCode))
		return
	}

	h.mu.Lock()
	ok := time.Now()
	h.status.LastSuccess = &ok
	h.status.ConsecutiveFailure = 0
	h.status.TotalSent++
	from := h.status.State
	h.status.State = "ok"
	h.mu.Unlock()
	if from != "ok" && from != "starting" {
		h.notify(from, "ok")
		log.Printf("[license] heartbeat recovered (%s -> ok)", from)
	}
}

// buildPayload 组装匿名心跳体。
func (h *Heartbeat) buildPayload() map[string]any {
	return map[string]any{
		"instance_id": h.opt.InstanceID,
		"version":     h.opt.Version,
		"ts":          time.Now().UTC().Format(time.RFC3339),
	}
}

func (h *Heartbeat) recordFailure(err error) {
	h.mu.Lock()
	h.status.TotalFailed++
	h.status.ConsecutiveFailure++
	from := h.status.State
	if h.status.ConsecutiveFailure >= h.opt.DegradedAfter {
		h.status.State = "degraded"
	}
	to := h.status.State
	fails := h.status.ConsecutiveFailure
	h.mu.Unlock()

	if to == "degraded" && from != "degraded" {
		h.notify(from, to)
		log.Printf("[license] heartbeat degraded: %v (consecutive=%d)", err, fails)
	}
}

func (h *Heartbeat) notify(from, to string) {
	if h.opt.OnStateChange != nil {
		h.opt.OnStateChange(from, to)
	}
}

type errStatus int

func (e errStatus) Error() string { return "heartbeat http status " + strconv.Itoa(int(e)) }
