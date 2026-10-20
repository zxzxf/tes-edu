package main

import (
	"log"

	"tes.edu/gateway/internal/config"
	"tes.edu/gateway/internal/router"
)

func main() {
	cfg := config.Load()
	r := router.New(cfg)
	addr := ":" + cfg.Port
	log.Printf("Tes Edu gateway listening on %s", addr)
	if err := r.Run(addr); err != nil {
		log.Fatal(err)
	}
}
