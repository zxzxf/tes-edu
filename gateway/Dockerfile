FROM golang:1.22 AS build
WORKDIR /src
ENV GOPROXY=https://goproxy.cn,direct
ENV GOTOOLCHAIN=local
# 先复制全部源码，再 go mod tidy：tidy 需要看到 .go 导入图才能补全 go.sum
# （新增的 github.com/redis/go-redis/v9 及其传递依赖会在此步自动拉取）
COPY . .
RUN go mod tidy
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
