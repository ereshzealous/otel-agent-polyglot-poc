package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/shopflow/analytics-service/internal/repository"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

func NewRouter() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("/health", healthHandler)
	mux.HandleFunc("/api/reports", reportsHandler)
	mux.HandleFunc("/api/events/summary", eventsSummaryHandler)
	mux.Handle("/metrics", promhttp.Handler())

	return otelhttp.NewHandler(mux, "analytics-service-http")
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "service": "analytics-service"})
}

func reportsHandler(w http.ResponseWriter, r *http.Request) {
	limit := 10
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil {
			limit = n
		}
	}

	reports, err := repository.GetRecentReports(r.Context(), limit)
	if err != nil {
		http.Error(w, "Failed to fetch reports: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(reports)
}

func eventsSummaryHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"message": "Event summary endpoint — query order_events table for details",
	})
}
