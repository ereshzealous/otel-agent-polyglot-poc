package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/shopflow/analytics-service/internal/db"
)

type OrderEvent struct {
	OrderID    string          `json:"orderId"`
	EventType  string          `json:"eventType"`
	CustomerID string          `json:"customerId"`
	ProductID  string          `json:"productId"`
	Quantity   int             `json:"quantity"`
	Amount     float64         `json:"amount"`
	Payload    json.RawMessage `json:"payload,omitempty"`
}

type SalesReport struct {
	ReportPeriod time.Time `json:"reportPeriod"`
	TotalOrders  int       `json:"totalOrders"`
	TotalRevenue float64   `json:"totalRevenue"`
	TopProduct   string    `json:"topProduct"`
	GeneratedAt  time.Time `json:"generatedAt"`
}

func SaveOrderEvent(ctx context.Context, event OrderEvent) error {
	payload, _ := json.Marshal(event)
	_, err := db.Pool.Exec(ctx, `
        INSERT INTO order_events (order_id, event_type, customer_id, product_id, quantity, amount, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    `, event.OrderID, event.EventType, event.CustomerID, event.ProductID,
		event.Quantity, event.Amount, payload)
	return err
}

func GenerateSalesReport(ctx context.Context) (*SalesReport, error) {
	report := &SalesReport{
		ReportPeriod: time.Now().Truncate(time.Minute),
		GeneratedAt:  time.Now(),
	}

	err := db.Pool.QueryRow(ctx, `
        SELECT COUNT(*), COALESCE(SUM(amount), 0)
        FROM order_events
        WHERE event_type = 'ORDER_CREATED'
          AND occurred_at >= NOW() - INTERVAL '1 hour'
    `).Scan(&report.TotalOrders, &report.TotalRevenue)
	if err != nil {
		return nil, fmt.Errorf("failed to aggregate orders: %w", err)
	}

	db.Pool.QueryRow(ctx, `
        SELECT product_id
        FROM order_events
        WHERE event_type = 'ORDER_CREATED'
          AND occurred_at >= NOW() - INTERVAL '1 hour'
          AND product_id IS NOT NULL
        GROUP BY product_id
        ORDER BY SUM(quantity) DESC
        LIMIT 1
    `).Scan(&report.TopProduct)

	_, err = db.Pool.Exec(ctx, `
        INSERT INTO sales_reports (report_period, total_orders, total_revenue, top_product, generated_at)
        VALUES ($1, $2, $3, $4, $5)
    `, report.ReportPeriod, report.TotalOrders, report.TotalRevenue, report.TopProduct, report.GeneratedAt)

	return report, err
}

func GetRecentReports(ctx context.Context, limit int) ([]SalesReport, error) {
	rows, err := db.Pool.Query(ctx, `
        SELECT report_period, total_orders, total_revenue, top_product, generated_at
        FROM sales_reports
        ORDER BY generated_at DESC
        LIMIT $1
    `, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var reports []SalesReport
	for rows.Next() {
		var r SalesReport
		rows.Scan(&r.ReportPeriod, &r.TotalOrders, &r.TotalRevenue, &r.TopProduct, &r.GeneratedAt)
		reports = append(reports, r)
	}
	return reports, nil
}
