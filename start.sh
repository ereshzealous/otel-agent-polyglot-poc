#!/bin/bash
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # reset

echo -e "${BOLD}${CYAN}========================================${NC}"
echo -e "${BOLD}${CYAN}  ShopFlow — OTel Polyglot Demo${NC}"
echo -e "${BOLD}${CYAN}========================================${NC}"
echo ""

echo -e "${YELLOW}[1/4] Checking Docker...${NC}"
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH"
    exit 1
fi
if ! docker info &> /dev/null; then
    echo "ERROR: Docker daemon is not running"
    exit 1
fi
echo -e "${GREEN}  Docker is ready${NC}"

echo -e "${YELLOW}[2/4] Cleaning up previous containers...${NC}"
docker compose down --remove-orphans 2>/dev/null || true
echo -e "${GREEN}  Cleanup done${NC}"

echo -e "${YELLOW}[3/4] Building and starting all services...${NC}"
docker compose up --build -d

echo -e "${YELLOW}[4/4] Waiting for services to become healthy...${NC}"

# polls a URL until it responds (or we run out of retries)
wait_for_service() {
    local name=$1
    local url=$2
    local max_retries=${3:-30}
    local retry=0

    while [ $retry -lt $max_retries ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ $name is up${NC}"
            return 0
        fi
        retry=$((retry + 1))
        sleep 2
    done
    echo -e "  ${YELLOW}⚠ $name not responding yet (may still be starting)${NC}"
    return 0
}

# infra needs a head start before the services can connect
echo -e "  Waiting for infrastructure..."
sleep 10

wait_for_service "Jaeger"          "http://localhost:16686"       15
wait_for_service "Prometheus"      "http://localhost:9090/-/ready" 15
wait_for_service "Grafana"         "http://localhost:3001/api/health" 15
wait_for_service "OTel Collector"  "http://localhost:13133"       15

# spring boot takes a while, give it extra time
echo -e "  Waiting for microservices..."
sleep 10

wait_for_service "Order Service"        "http://localhost:8080/api/orders/health" 30
wait_for_service "Inventory Service"    "http://localhost:8081/health"            30
wait_for_service "Notification Service" "http://localhost:8082/health"            30
wait_for_service "Analytics Service"    "http://localhost:8083/health"            30

echo ""
echo -e "${BOLD}${GREEN}========================================${NC}"
echo -e "${BOLD}${GREEN}  All services are running!${NC}"
echo -e "${BOLD}${GREEN}========================================${NC}"
echo ""
echo -e "${BOLD}Service Endpoints:${NC}"
echo -e "  Order Service        → http://localhost:8080/api/orders"
echo -e "  Inventory Service    → http://localhost:8081/api/products"
echo -e "  Notification Service → http://localhost:8082/api/notifications/status"
echo -e "  Analytics Service    → http://localhost:8083/api/reports"
echo ""
echo -e "${BOLD}Observability Dashboards:${NC}"
echo -e "  Jaeger UI     → http://localhost:16686"
echo -e "  Prometheus    → http://localhost:9090"
echo -e "  Grafana       → http://localhost:3001  (admin/admin)"
echo -e "  Kafka UI      → http://localhost:8090"
echo ""
echo -e "${BOLD}Quick Test:${NC}"
echo -e "  curl -X POST http://localhost:8080/api/orders \\"
echo -e "    -H 'Content-Type: application/json' \\"
echo -e "    -d '{\"customerId\":\"CUST-001\",\"productId\":\"PROD-001\",\"quantity\":2,\"unitPrice\":1299.99}'"
echo ""
echo -e "See ${BOLD}TESTING.md${NC} for full test commands and observability guide."
echo ""
echo -e "To stop: ${BOLD}docker compose down${NC}"
