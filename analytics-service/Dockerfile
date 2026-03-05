FROM golang:1.22-alpine AS build
WORKDIR /app
RUN apk add --no-cache git

COPY go.mod ./
COPY . .
RUN go mod tidy && go mod download
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o analytics-service ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates tzdata
WORKDIR /app
COPY --from=build /app/analytics-service .
ENTRYPOINT ["./analytics-service"]
