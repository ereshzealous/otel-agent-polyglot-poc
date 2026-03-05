#!/bin/bash
set -e

# each service gets its own database - keeps things isolated
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE DATABASE ordersdb;
  CREATE DATABASE inventorydb;
  CREATE DATABASE analyticsdb;

  -- order-service tables
  \connect ordersdb
  CREATE TABLE orders (
    id VARCHAR(255) PRIMARY KEY,
    customer_id VARCHAR(100) NOT NULL,
    product_id  VARCHAR(100) NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL,
    total_amount NUMERIC(12,2) NOT NULL,
    status      VARCHAR(50) DEFAULT 'PENDING',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
  );
  CREATE INDEX idx_orders_customer ON orders(customer_id);
  CREATE INDEX idx_orders_status   ON orders(status);

  -- inventory-service tables + seed data
  \connect inventorydb
  CREATE TABLE products (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0,
    reserved    INTEGER NOT NULL DEFAULT 0,
    unit_price  NUMERIC(10,2) NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW()
  );
  INSERT INTO products VALUES
    ('PROD-001', 'Laptop Pro 15',   50,  0, 1299.99, NOW()),
    ('PROD-002', 'Wireless Mouse',  200, 0, 29.99,   NOW()),
    ('PROD-003', 'USB-C Hub',       150, 0, 49.99,   NOW()),
    ('PROD-004', 'Mechanical Keyboard', 75, 0, 149.99, NOW()),
    ('PROD-005', '4K Monitor',      30, 0, 599.99,  NOW());

  -- analytics-service tables (events + periodic reports)
  \connect analyticsdb
  CREATE TABLE order_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    VARCHAR(100) NOT NULL,
    event_type  VARCHAR(100) NOT NULL,
    customer_id VARCHAR(100),
    product_id  VARCHAR(100),
    quantity    INTEGER,
    amount      NUMERIC(12,2),
    payload     JSONB,
    occurred_at TIMESTAMP DEFAULT NOW()
  );
  CREATE TABLE sales_reports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_period TIMESTAMP NOT NULL,
    total_orders  INTEGER DEFAULT 0,
    total_revenue NUMERIC(14,2) DEFAULT 0,
    top_product   VARCHAR(100),
    generated_at  TIMESTAMP DEFAULT NOW()
  );
EOSQL
