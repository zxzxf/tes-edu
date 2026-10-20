FROM golang:1.22 AS build
WORKDIR /src
ENV GOPROXY=https://goproxy.cn,direct
ENV GOTOOLCHAIN=local
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /gateway .

FROM alpine:3.20
RUN sed -i 's|https://dl-cdn.alpinelinux.org|https://mirrors.aliyun.com|g' /etc/apk/repositories \
 && apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=build /gateway /app/gateway
COPY config.yaml /app/config.yaml
ENV CONFIG_PATH=/app/config.yaml
ENV JWT_SECRET=""
EXPOSE 8080
ENTRYPOINT ["/app/gateway"]
