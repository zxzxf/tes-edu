// mock backend for POC verification (run separately on :8000)
// 仅用于本地验证网关转发，非生产代码。
package main

import (
	"fmt"
	"net/http"
)

func main() {
	http.HandleFunc("/solve", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, `{"answer":"mock: 一元二次方程 x^2-3x+2=0 的解为 x=1, x=2","source":"mock-agent"}`)
	})
	http.HandleFunc("/api/app/ping", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, `{"msg":"pong from agent"}`)
	})
	http.ListenAndServe(":8000", nil)
}
