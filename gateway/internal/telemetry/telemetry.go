// Package telemetry 实现匿名运行指标回传（P5 合同硬要求 3/3：
// "保留匿名运行指标回传（仅指标与错误码，不含学生内容）"）。
//
// 行为：
//   - 后台 goroutine 按 Interval 周期 POST metrics.Snapshot() 到回传端点；
//   - 上报内容 = 计数器键值 + 实例 ID + 版本 + 时间戳，字段白名单由
//     metrics 包常量强制（无路径/uid/租户/题干）；
//   - 回传失败只记日志，绝不影响请求转发（纯旁路）；
//   - 保留本地快照供 /metricsz 暴露（运维本机查看）。
package telemetry

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"time"

	"gateway/internal/metrics"
)

// Options 回传选项。
type Options struct {
	// Enabled 总开关。
	Enabled bool
	// URL 指标接收端（我方遥测服务）。
	URL string
	// Interval 回传周期。默认 1h。
	Interval time.Duration
	// Timeout 单次回传超时。默认 5s。
	Timeout time.Duration
	// InstanceID 匿名实例标识。
	InstanceID string
	// Version 网关版本号。
	Version string
	// Client 可注入 HTTP 客户端（测试用）。
	Client *http.Client
}

// Reporter 指标回传器。
type Reporter struct {
	opt Options
}

// New 构造回传器并补全默认值。
func New(opt Options) *Reporter {
	if opt.Interval <= 0 {
		opt.Interval = time.Hour
	}
	if opt.Timeout <= 0 {
		opt.Timeout = 5 * time.Second
	}
	if opt.Client == nil {
		opt.Client = &http.Client{Timeout: opt.Timeout}
	}
	return &Reporter{opt: opt}
}

// Start 启动后台回传循环（部署即先报一次，再按周期）。
func (r *Reporter) Start(ctx context.Context) {
	go func() {
		r.report()
		ticker := time.NewTicker(r.opt.Interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				r.report()
			}
		}
	}()
}

// report 执行一次回传（导出仅用于测试手动触发）。
func (r *Reporter) report() {
	payload := map[string]any{
		"instance_id": r.opt.InstanceID,
		"version":     r.opt.Version,
		"ts":          time.Now().UTC().Format(time.RFC3339),
		"counters":    metrics.Snapshot(),
	}
	body, err := json.Marshal(payload)
	if err != nil {
		log.Printf("[telemetry] marshal: %v", err)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), r.opt.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.opt.URL, bytes.NewReader(body))
	if err != nil {
		log.Printf("[telemetry] build request: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := r.opt.Client.Do(req)
	if err != nil {
		// 失败静默（仅日志）：遥测不可用不影响网关
		log.Printf("[telemetry] report failed: %v", err)
		return
	}
	_ = resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		log.Printf("[telemetry] report rejected: status %d", resp.StatusCode)
	}
}
