package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/shopflow/analytics-service/internal/api"
	"github.com/shopflow/analytics-service/internal/db"
	kafkaconsumer "github.com/shopflow/analytics-service/internal/kafka"
	"github.com/shopflow/analytics-service/internal/scheduler"
	"github.com/shopflow/analytics-service/telemetry"
)

func main() {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	otlpEndpoint := strings.TrimPrefix(
		getEnv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317"), "http://",
	)

	shutdownTelemetry, err := telemetry.Init(ctx,
		getEnv("OTEL_SERVICE_NAME", "analytics-service"),
		getEnv("OTEL_SERVICE_VERSION", "1.0.0"),
		otlpEndpoint,
	)
	if err != nil {
		log.Fatalf("Failed to init telemetry: %v", err)
	}
	defer func() {
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		shutdownTelemetry(shutdownCtx)
	}()

	if err := db.Connect(ctx); err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Pool.Close()
	log.Println("Connected to PostgreSQL")

	kafkaconsumer.StartConsumer(ctx)

	intervalSeconds := 60
	if s := os.Getenv("CRON_INTERVAL_SECONDS"); s != "" {
		if n, err := strconv.Atoi(s); err == nil {
			intervalSeconds = n
		}
	}
	cronJob := scheduler.StartScheduler(ctx, intervalSeconds)
	defer cronJob.Stop()

	port := getEnv("PORT", "8083")
	server := &http.Server{
		Addr:         fmt.Sprintf(":%s", port),
		Handler:      api.NewRouter(),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	go func() {
		log.Printf("Analytics service listening on :%s", port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("HTTP server error: %v", err)
		}
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	<-sigCh

	log.Println("Shutting down analytics service...")
	cancel()
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	server.Shutdown(shutdownCtx)
	log.Println("Analytics service stopped")
}

func getEnv(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}
